"""盘中估算 vs 真实净值偏差追踪

持久化存储: report_log/estimate_bias.json

数据流:
  1. scheduler 每 30s 拿到新的 estimate_pct 时, record_estimate(code, pct, time)
     覆盖同一天已存在的值 → 留下收盘前最后一次估算
  2. 基金历史净值刷新时, reconcile_with_history(code, history_df)
     找出已有 estimate 但未核对的日期, 配对 actual change_pct → 生成 bias 记录
  3. get_stats(code) 计算最近 20 个已核对日的 MAE + 偏差方向 + 置信度

无任何调用方阻塞, 错误只记日志不抛。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from fund_advisor.utils.logger import get_logger


log = get_logger("fund_advisor.estimate_bias")

_PATH = Path("report_log/estimate_bias.json")
_LOCK = threading.Lock()
_DATA: Optional[Dict[str, Dict[str, Any]]] = None

# 保留窗口
_KEEP_ESTIMATE_DAYS = 90
_KEEP_RECONCILED = 60
_WINDOW = 20


# ---------------------------------------------------------------------------
# 磁盘层
# ---------------------------------------------------------------------------

def _load() -> Dict[str, Dict[str, Any]]:
    global _DATA
    if _DATA is not None:
        return _DATA
    if _PATH.exists():
        try:
            _DATA = json.loads(_PATH.read_text(encoding="utf-8"))
            if not isinstance(_DATA, dict):
                _DATA = {}
        except Exception as e:  # noqa: BLE001
            log.warning(f"estimate_bias 加载失败, 重新初始化: {e}")
            _DATA = {}
    else:
        _DATA = {}
    return _DATA


def flush() -> None:
    """原子落盘 (tmp + replace)。scheduler 每轮 funds tick 末尾调一次。"""
    with _LOCK:
        if _DATA is None:
            return
        try:
            _PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(_DATA, ensure_ascii=False), encoding="utf-8")
            tmp.replace(_PATH)
        except Exception as e:  # noqa: BLE001
            log.warning(f"estimate_bias 落盘失败: {e}")


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _parse_est_time(est_time: str) -> Optional[str]:
    """天天基金 gztime 格式:
       "2026-05-07 15:00" / "2026-05-07 15:00:00" / "05-07 15:00" (少数场景)
       统一返回 "YYYY-MM-DD"
    """
    if not est_time:
        return None
    s = str(est_time).strip()
    try:
        if len(s) >= 10 and s[4] == "-":
            return s[:10]
        if len(s) >= 5 and s[2] == "-":
            year = datetime.now().year
            return f"{year}-{s[:5]}"
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# 记录
# ---------------------------------------------------------------------------

def record_estimate(code: str, est_pct: float, est_time: str) -> None:
    if code is None or not isinstance(est_pct, (int, float)):
        return
    d = _parse_est_time(est_time)
    if not d:
        return
    with _LOCK:
        data = _load()
        entry = data.setdefault(str(code), {"estimates": {}, "reconciled": []})
        entry["estimates"][d] = float(est_pct)
        cutoff = (datetime.now().date() - timedelta(days=_KEEP_ESTIMATE_DAYS)).isoformat()
        entry["estimates"] = {k: v for k, v in entry["estimates"].items() if k >= cutoff}


def reconcile_with_history(code: str, history_df: pd.DataFrame) -> None:
    """把已有 estimate 但未核对的日期, 在 history 里找真实 change_pct 并写入 reconciled"""
    if history_df is None or not isinstance(history_df, pd.DataFrame) or history_df.empty:
        return
    if "date" not in history_df.columns or "change_pct" not in history_df.columns:
        return
    with _LOCK:
        data = _load()
        entry = data.setdefault(str(code), {"estimates": {}, "reconciled": []})
        seen = {r.get("date") for r in (entry.get("reconciled") or [])}
        estimates: Dict[str, float] = entry.get("estimates") or {}
        today = datetime.now().date().isoformat()

        for _, row in history_df.iterrows():
            d_raw = row.get("date")
            if isinstance(d_raw, pd.Timestamp):
                d = d_raw.strftime("%Y-%m-%d")
            else:
                d = str(d_raw)[:10]
            if not d or d in seen:
                continue
            if d == today:
                # 当天真实净值要到 20:00+ 才公布, 跳过
                continue
            actual = row.get("change_pct")
            if actual is None or (isinstance(actual, float) and pd.isna(actual)):
                continue
            try:
                actual_f = float(actual)
            except (TypeError, ValueError):
                continue
            est = estimates.get(d)
            if est is None:
                continue
            entry["reconciled"].append({
                "date": d,
                "est": round(float(est), 4),
                "actual": round(actual_f, 4),
                "abs_err": round(abs(actual_f - float(est)), 4),
            })
        entry["reconciled"].sort(key=lambda r: r.get("date", ""))
        entry["reconciled"] = entry["reconciled"][-_KEEP_RECONCILED:]


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def get_stats(code: str) -> Dict[str, Any]:
    """
    返回:
      n           已核对天数
      mae         平均绝对误差 (%)
      bias        有向偏差, 正值表示估算偏高
      confidence  low / medium / high
      last_err    最近一次误差
    """
    with _LOCK:
        data = _load()
        entry = data.get(str(code))
    if not entry:
        return {"n": 0, "confidence": "low"}
    rec = entry.get("reconciled") or []
    recent = rec[-_WINDOW:]
    n = len(recent)
    if n == 0:
        return {"n": 0, "confidence": "low"}
    mae = sum(r.get("abs_err", 0.0) for r in recent) / n
    bias = sum(r.get("est", 0.0) - r.get("actual", 0.0) for r in recent) / n
    if n >= 15:
        conf = "high"
    elif n >= 8:
        conf = "medium"
    else:
        conf = "low"
    return {
        "n": n,
        "mae": round(mae, 3),
        "bias": round(bias, 3),
        "confidence": conf,
        "last_err": round(recent[-1].get("abs_err", 0.0), 3) if recent else None,
    }
