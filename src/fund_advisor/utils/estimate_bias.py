"""盘中估算偏差追踪 (SQLite 后端)

这个模块仅做函数签名兼容, 真实存储委托给 fund_advisor.data.db。
"""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from fund_advisor.data import db


def record_estimate(code: str, est_pct: float, est_time: str) -> None:
    db.record_estimate(code, est_pct, est_time)


def reconcile_with_history(code: str, history_df: pd.DataFrame) -> None:
    db.reconcile_bias(code, history_df)


def get_stats(code: str) -> Dict[str, Any]:
    return db.get_bias_stats(code)


def flush() -> None:
    # SQLite 写时已落盘; 保留空实现兼容旧调用
    return None
