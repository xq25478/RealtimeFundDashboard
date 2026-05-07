"""Claude 流式咨询 handler

用户在 UI 问问题时:
  1. 从 StateStore 拿完整快照作为上下文
  2. 若问题中出现基金代码 (6 位), 先强制 update_fund(code) 重拉一次
  3. 用精简版 prompt 调用 client.messages.stream, 把 text 片段 yield 出去
     让 Flask 以 SSE / chunked response 推给前端
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Generator, List, Optional

import pandas as pd

from fund_advisor.utils.config import load_config
from fund_advisor.utils.logger import get_logger
from fund_advisor.server.state import get_store, StateStore
from fund_advisor.advisor.llm_client import get_client, get_model
from fund_advisor.advisor.prompt import SYSTEM_PROMPT
from fund_advisor.cli import _summarize_fund


log = get_logger("fund_advisor.chat")

# A 股基金代码固定 6 位
_CODE_RE = re.compile(r"\b(\d{6})\b")


def _extract_codes(text: str) -> List[str]:
    return list(dict.fromkeys(_CODE_RE.findall(text or "")))


def _refresh_fund_inplace(store: StateStore, code: str, cfg) -> bool:
    """实时重拉单只基金 (配置里存在才拉)"""
    fund = next((f for f in cfg.funds if f.code == code), None)
    if fund is None:
        return False
    try:
        summary, _tech, _hist = _summarize_fund(fund)
        store.update_fund(code, summary)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning(f"chat 重拉基金 {code} 失败: {e}")
        store.push_error(f"chat_refresh_{code}", str(e))
        return False


def _format_market_block(snap: Dict[str, Any]) -> str:
    m = snap.get("market") or {}
    tech = snap.get("market_tech") or {}
    lines = ["## 大盘"]
    for name, info in (m.get("indices") or {}).items():
        pct = info.get("change_pct")
        pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "-"
        price = info.get("price")
        price_str = f"{price:.2f}" if isinstance(price, (int, float)) else "-"
        lines.append(f"- {name}: {price_str} ({pct_str})")
    if tech.get("trend"):
        lines.append(f"- 技术面: {tech.get('trend')} / {tech.get('ma_signal','')}")
    north = m.get("north_money_total")
    if isinstance(north, (int, float)):
        lines.append(f"- 北向资金: {north/1e8:+.1f} 亿")
    breadth = m.get("breadth") or {}
    if breadth:
        ur = breadth.get("up_ratio")
        if isinstance(ur, (int, float)):
            lines.append(f"- 涨跌比: {ur*100:.0f}% 上涨")
    sectors = m.get("sectors") or []
    if sectors:
        top = sorted(sectors, key=lambda r: (r.get("main_net_flow") or 0), reverse=True)[:5]
        lines.append("- 主流入板块: " + ", ".join(
            f"{r.get('sector')}({(r.get('main_net_flow') or 0)/1e8:+.1f}亿)" for r in top
        ))
    return "\n".join(lines)


def _format_funds_block(snap: Dict[str, Any]) -> str:
    funds = snap.get("funds") or []
    decisions = {d.get("fund_code"): d for d in (snap.get("fund_decisions") or [])}
    holdings = snap.get("fund_holdings") or {}
    if not funds:
        return "## 自选基金\n(暂无数据)"
    lines = ["## 自选基金"]
    for f in funds:
        code = f.get("code")
        pct = f.get("estimate_pct")
        pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "-"
        nav = f.get("last_nav")
        nav_str = f"{nav:.4f}" if isinstance(nav, (int, float)) else "-"
        dec = decisions.get(code, {})
        action = dec.get("action", "-")
        score = dec.get("score")
        score_str = f"{score:+.0f}" if isinstance(score, (int, float)) else "-"
        lines.append(
            f"- [{code}] {f.get('name','')} | 主题:{f.get('theme','-')} | "
            f"净值 {nav_str} 盘中 {pct_str} | 决策:{action} 评分 {score_str}"
        )
        reasons = dec.get("reasons") or []
        if reasons:
            lines.append(f"  理由: {'; '.join(reasons[:3])}")
        h = (holdings.get(code) or {})
        attr = h.get("attribution") or []
        if attr:
            hot = ", ".join(f"{a.get('name')}({a.get('change_pct','')}%)" for a in attr[:3])
            lines.append(f"  重仓归因: {hot}")
    return "\n".join(lines)


def _format_sentiment_block(snap: Dict[str, Any]) -> str:
    s = snap.get("sentiment") or {}
    p = snap.get("policy") or {}
    lines = ["## 消息面 & 政策面"]
    if s.get("summary"):
        lines.append(f"- 消息面: {s.get('summary')} (评分 {s.get('score', 0):+.0f})")
    if p.get("summary"):
        lines.append(f"- 政策面: {p.get('summary')} (评分 {p.get('score', 0):+.0f})")
    news = snap.get("news") or []
    if news:
        lines.append("- 近期热点:")
        for n in news[:8]:
            title = n.get("title") or ""
            if title:
                lines.append(f"  · {title}")
    return "\n".join(lines)


def build_chat_context(snap: Dict[str, Any]) -> str:
    """组装给 Claude 的快照摘要 (简版, 控制 token)"""
    ts = snap.get("updated_at")
    ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) and ts else "-"
    blocks = [
        f"当前快照时间: {ts_str}",
        _format_market_block(snap),
        _format_funds_block(snap),
        _format_sentiment_block(snap),
    ]
    return "\n\n".join(b for b in blocks if b)


def stream_chat(
    messages: List[Dict[str, str]],
    *,
    mention_codes: Optional[List[str]] = None,
    config_path: str = "config/holdings.yaml",
) -> Generator[str, None, None]:
    """流式生成 Claude 回复, 每次 yield 一段文本

    messages: 前端传来的历史, [{role: user|assistant, content: str}, ...]
    mention_codes: 前端预先识别好的基金代码; 若为空, 从最后一条 user 消息自动抽取
    """
    store = get_store()
    cfg = load_config(config_path)

    # 1) 识别 + 强制重拉基金
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    codes = list(mention_codes or [])
    if last_user:
        codes.extend(c for c in _extract_codes(last_user.get("content", "")) if c not in codes)
    refreshed: List[str] = []
    for code in codes:
        if _refresh_fund_inplace(store, code, cfg):
            refreshed.append(code)
    if refreshed:
        yield json.dumps({"type": "refresh", "codes": refreshed}, ensure_ascii=False) + "\n"

    # 2) 组装 context
    snap = store.snapshot()
    context_md = build_chat_context(snap)

    # 3) 构造 Claude 请求
    client = get_client()
    model = get_model()

    system_prompt = (
        SYSTEM_PROMPT
        + "\n\n你现在作为实时基金看板的咨询助手, 回答要直接、简练, 多用 Markdown 列表, "
        "必要时引用数据。若用户问某只基金, 务必结合下方快照里的实时数据给出明确结论。"
    )

    claude_messages: List[Dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"下面是当前实时看板快照, 用作回答依据:\n\n{context_md}\n\n"
                "接下来是用户的问题, 请作答。"
            ),
        },
        {"role": "assistant", "content": "已收到看板快照, 请问。"},
    ]
    for m in messages:
        role = m.get("role")
        if role in ("user", "assistant") and m.get("content"):
            claude_messages.append({"role": role, "content": m["content"]})

    request_params: Dict[str, Any] = {
        "model": model,
        "max_tokens": 4000,
        "system": system_prompt,
        "messages": claude_messages,
    }
    m_norm = model.lower().replace(".", "-").replace("_", "-")
    if any(tag in m_norm for tag in ("opus-4-6", "opus-4-7", "sonnet-4-6")):
        request_params["thinking"] = {"type": "adaptive"}
    if any(tag in m_norm for tag in ("opus-4-6", "opus-4-7")):
        request_params["output_config"] = {"effort": "high"}

    # 4) 流式
    try:
        with client.messages.stream(**request_params) as s:
            for text in s.text_stream:
                if text:
                    yield json.dumps({"type": "delta", "text": text}, ensure_ascii=False) + "\n"
    except TypeError:
        params = dict(request_params)
        params.pop("thinking", None)
        params.pop("output_config", None)
        with client.messages.stream(**params) as s:
            for text in s.text_stream:
                if text:
                    yield json.dumps({"type": "delta", "text": text}, ensure_ascii=False) + "\n"
    except Exception as e:  # noqa: BLE001
        log.error(f"Claude 流式失败: {e}")
        store.push_error("chat", str(e))
        yield json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False) + "\n"
        return

    yield json.dumps({"type": "done"}, ensure_ascii=False) + "\n"
