"""每日决策持久化 + 简单回测.

存储格式: reports/history/decisions.jsonl, 每行一条决策记录.

字段:
    date            决策日期 YYYY-MM-DD
    code, name      基金代码/名称
    theme           主题
    action          买入/加仓/持有/减仓/卖出
    score           综合评分
    confidence      高/中/低
    nav_at          当日单位净值
    estimate_pct    当日盘中估算涨跌幅(%)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from fund_advisor.utils.logger import get_logger

log = get_logger(__name__)

HISTORY_DIR = Path("reports/history")
DECISIONS_PATH = HISTORY_DIR / "decisions.jsonl"


def append_decisions(
    decisions: Iterable,
    fund_summaries: List[Dict],
    *,
    date: Optional[str] = None,
) -> int:
    """把当日 decisions 追加到 jsonl.

    返回写入条数.
    """
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    date = date or datetime.now().strftime("%Y-%m-%d")

    summary_map = {s.get("code"): s for s in fund_summaries}

    n = 0
    with DECISIONS_PATH.open("a", encoding="utf-8") as f:
        for d in decisions:
            s = summary_map.get(d.fund_code, {})
            row = {
                "date": date,
                "code": d.fund_code,
                "name": d.fund_name,
                "theme": s.get("theme", ""),
                "action": d.action,
                "score": round(float(d.score), 2),
                "confidence": d.confidence,
                "nav_at": s.get("last_nav"),
                "estimate_pct": s.get("estimate_pct"),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    log.debug(f"已追加 {n} 条决策到 {DECISIONS_PATH}")
    return n


def load_history(path: Path = DECISIONS_PATH) -> List[Dict]:
    """读取所有历史决策"""
    if not path.exists():
        return []
    out: List[Dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def backtest(*, lookback_days: int = 90) -> Dict:
    """简单回测: 看每条历史 decision 之后 5 / 20 日基金净值变化,
    计算每种 action 的胜率(方向与建议一致的比例).

    返回 { action: {n, win_5d, win_20d, avg_return_5d, avg_return_20d} }
    """
    import pandas as pd
    from fund_advisor.data.fund import get_fund_history

    rows = load_history()
    if not rows:
        return {}

    cutoff = pd.Timestamp.now() - pd.Timedelta(days=lookback_days * 2)
    rows = [r for r in rows if pd.Timestamp(r.get("date", "1970-01-01")) >= cutoff]

    # 同代码缓存历史净值, 避免重复拉
    nav_cache: Dict[str, "pd.DataFrame"] = {}

    def _nav_after(code: str, base_date: str, days: int) -> Optional[float]:
        df = nav_cache.get(code)
        if df is None:
            try:
                df = get_fund_history(code, days=lookback_days + 60)
            except Exception:
                df = pd.DataFrame()
            nav_cache[code] = df
        if df is None or df.empty or "date" not in df.columns or "nav" not in df.columns:
            return None
        try:
            base_ts = pd.Timestamp(base_date)
        except Exception:
            return None
        future = df[df["date"] >= base_ts + pd.Timedelta(days=days)]
        if future.empty:
            return None
        return float(future.iloc[0]["nav"])

    def _expect_up(action: str) -> Optional[bool]:
        """买入/加仓 期望上涨; 减仓/卖出 期望下跌; 持有 不计."""
        if action in ("买入", "加仓"):
            return True
        if action in ("减仓", "卖出"):
            return False
        return None

    out: Dict[str, Dict] = {}
    for r in rows:
        action = r.get("action")
        expect = _expect_up(action)
        nav_at = r.get("nav_at")
        if expect is None or not isinstance(nav_at, (int, float)) or nav_at <= 0:
            continue
        nav5 = _nav_after(r["code"], r["date"], 5)
        nav20 = _nav_after(r["code"], r["date"], 20)
        if nav5 is None and nav20 is None:
            continue

        slot = out.setdefault(action, {
            "n": 0, "win_5d": 0, "win_20d": 0,
            "ret_5d_sum": 0.0, "ret_5d_n": 0,
            "ret_20d_sum": 0.0, "ret_20d_n": 0,
        })
        slot["n"] += 1
        if nav5 is not None:
            ret = (nav5 - nav_at) / nav_at * 100
            slot["ret_5d_sum"] += ret
            slot["ret_5d_n"] += 1
            if (ret > 0) == expect:
                slot["win_5d"] += 1
        if nav20 is not None:
            ret = (nav20 - nav_at) / nav_at * 100
            slot["ret_20d_sum"] += ret
            slot["ret_20d_n"] += 1
            if (ret > 0) == expect:
                slot["win_20d"] += 1

    # 汇总
    summary: Dict[str, Dict] = {}
    for action, slot in out.items():
        n = slot["n"]
        summary[action] = {
            "n": n,
            "win_rate_5d":  slot["win_5d"]  / slot["ret_5d_n"]  if slot["ret_5d_n"]  else None,
            "win_rate_20d": slot["win_20d"] / slot["ret_20d_n"] if slot["ret_20d_n"] else None,
            "avg_return_5d":  slot["ret_5d_sum"]  / slot["ret_5d_n"]  if slot["ret_5d_n"]  else None,
            "avg_return_20d": slot["ret_20d_sum"] / slot["ret_20d_n"] if slot["ret_20d_n"] else None,
        }
    return summary
