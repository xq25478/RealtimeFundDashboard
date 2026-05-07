"""LLM 投资顾问主入口：调用 Claude 生成基金操作指南"""

from typing import Dict, List, Optional

import pandas as pd
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from fund_advisor.utils.logger import get_logger
from fund_advisor.advisor.llm_client import get_client, get_model
from fund_advisor.advisor.prompt import SYSTEM_PROMPT, build_user_prompt
from fund_advisor.analysis.decision import Decision
from fund_advisor.analysis.technical import TechnicalSignal
from fund_advisor.analysis.sentiment import SentimentResult
from fund_advisor.analysis.policy import PolicySignal


log = get_logger(__name__)


def _supports_adaptive_thinking(model: str) -> bool:
    """Opus 4.6+ / Sonnet 4.6+ 才支持 adaptive thinking"""
    if not model:
        return False
    m = model.lower().replace(".", "-").replace("_", "-")
    return any(tag in m for tag in ("opus-4-6", "opus-4-7", "sonnet-4-6"))


def _supports_max_effort(model: str) -> bool:
    """effort='max' 仅 Opus(4.6/4.7) 支持,Sonnet 会 400"""
    if not model:
        return False
    m = model.lower().replace(".", "-").replace("_", "-")
    return any(tag in m for tag in ("opus-4-6", "opus-4-7"))


def generate_advice(
    *,
    market_data: Dict,
    sector_data: List[Dict],
    fund_summaries: List[Dict],
    decisions: List[Decision],
    sentiment: SentimentResult,
    policy: PolicySignal,
    north_money: float,
    market_tech: TechnicalSignal,
    news_top: List[Dict],
    index_histories: Optional[Dict[str, pd.DataFrame]] = None,
    fund_histories: Optional[Dict[str, pd.DataFrame]] = None,
    north_history: Optional[pd.DataFrame] = None,
    policy_news_recent: Optional[List[Dict]] = None,
    margin_history: Optional[pd.DataFrame] = None,
    breadth: Optional[Dict] = None,
    overseas: Optional[Dict[str, Dict]] = None,
    valuations: Optional[Dict[str, Dict]] = None,
    fund_holdings: Optional[Dict[str, Dict]] = None,
    etf_premium: Optional[Dict[str, Dict]] = None,
    data_health: Optional[Dict[str, str]] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = 32000,
    stream: bool = True,
) -> str:
    """调用 Claude 生成基金操作指南，返回完整文本"""
    client = get_client(api_key=api_key, base_url=base_url)
    model = model or get_model()

    user_prompt = build_user_prompt(
        market_data=market_data,
        sector_data=sector_data,
        fund_summaries=fund_summaries,
        decisions=decisions,
        sentiment=sentiment,
        policy=policy,
        north_money=north_money,
        market_tech=market_tech,
        news_top=news_top,
        index_histories=index_histories,
        fund_histories=fund_histories,
        north_history=north_history,
        policy_news_recent=policy_news_recent,
        margin_history=margin_history,
        breadth=breadth,
        overseas=overseas,
        valuations=valuations,
        fund_holdings=fund_holdings,
        etf_premium=etf_premium,
        data_health=data_health,
    )

    # 27+ 只基金时, 输出篇幅可能逼近 max_tokens, 提前抬高
    n_funds = len(fund_summaries) if fund_summaries else 0
    if n_funds >= 30 and max_tokens < 48000:
        max_tokens = 48000

    log.info(
        f"调用 Claude [{model}] 生成投资指南... "
        f"(上下文 ~{len(user_prompt):,} 字符, 基金 {n_funds} 只, max_tokens={max_tokens})"
    )

    request_params = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": user_prompt}],
    }

    if _supports_adaptive_thinking(model):
        request_params["thinking"] = {"type": "adaptive"}

    # Opus 4.6/4.7 才能用 effort=max,Sonnet 用默认 high(避免 400)
    if _supports_max_effort(model):
        request_params["output_config"] = {"effort": "max"}

    if stream:
        return _stream_and_collect(client, request_params)

    msg = client.messages.create(**request_params)
    return _extract_text(msg.content)


def _stream_and_collect(client, request_params) -> str:
    """流式拉取，最终返回完整文本（避免长输出超时）"""
    chunks: List[str] = []
    try:
        with client.messages.stream(**request_params) as s:
            for text in s.text_stream:
                chunks.append(text)
    except TypeError:
        # 部分代理网关不支持 cache_control / 高级参数, 兜底为简化请求
        params = dict(request_params)
        if isinstance(params.get("system"), list):
            params["system"] = "\n".join(
                b.get("text", "") for b in params["system"] if isinstance(b, dict)
            )
        params.pop("thinking", None)
        params.pop("output_config", None)
        chunks = []
        with client.messages.stream(**params) as s:
            for text in s.text_stream:
                chunks.append(text)
    return "".join(chunks)


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    parts: List[str] = []
    for block in content:
        if hasattr(block, "type") and block.type == "text":
            parts.append(block.text)
        elif hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


def render_advice(text: str, console: Optional[Console] = None):
    """命令行美化展示 LLM 输出"""
    console = console or Console()
    console.print()
    console.print(Panel.fit("[bold cyan]🤖 Claude AI 投资指南[/]", border_style="cyan"))
    console.print(Markdown(text))
    console.print()
