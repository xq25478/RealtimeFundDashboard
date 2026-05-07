"""SQLite 持久化: 新闻 + 大盘日级数据 + 盘中估算偏差

保留 365 天, scheduler 在 _tick_valuations 末尾触发 cleanup。
单进程 WAL, 模块内锁串行化写。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from fund_advisor.utils.logger import get_logger


log = get_logger("fund_advisor.db")

_PATH = Path("data/fund.db")
_RETENTION_DAYS = 365
_VACUUM_INTERVAL_DAYS = 7

_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None
_LAST_VACUUM: float = 0.0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    event_time TEXT,
    title TEXT,
    summary TEXT,
    source TEXT,
    category TEXT,
    hash TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_news_cat_ts ON news(category, ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_ts ON news(ts);

CREATE TABLE IF NOT EXISTS index_daily (
    date TEXT, code TEXT, name TEXT,
    close REAL, change_pct REAL,
    PRIMARY KEY (date, code)
);
CREATE INDEX IF NOT EXISTS idx_index_daily_date ON index_daily(date DESC);

CREATE TABLE IF NOT EXISTS north_daily (
    date TEXT PRIMARY KEY,
    net REAL
);

CREATE TABLE IF NOT EXISTS breadth_daily (
    date TEXT PRIMARY KEY,
    up_ratio REAL, limit_up INTEGER, limit_down INTEGER,
    median_change REAL,
    payload TEXT
);

CREATE TABLE IF NOT EXISTS valuation_daily (
    date TEXT, name TEXT,
    pe REAL, pb REAL,
    pe_percentile REAL, pb_percentile REAL,
    PRIMARY KEY (date, name)
);

CREATE TABLE IF NOT EXISTS margin_daily (
    date TEXT PRIMARY KEY,
    balance REAL, financing_balance REAL
);

CREATE TABLE IF NOT EXISTS fund_estimate (
    code TEXT, date TEXT,
    est_pct REAL, est_time TEXT,
    PRIMARY KEY (code, date)
);

CREATE TABLE IF NOT EXISTS fund_bias (
    code TEXT, date TEXT,
    est_pct REAL, actual_pct REAL, abs_err REAL,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_fund_bias_code_date ON fund_bias(code, date DESC);
"""


# ---------------------------------------------------------------------------
# 连接
# ---------------------------------------------------------------------------

def _ensure_parent() -> None:
    parent = _PATH.parent
    if parent.exists() and not parent.is_dir():
        # 同名文件挡着就挪开, 保留旧内容
        backup = parent.with_name(parent.name + ".legacy")
        try:
            parent.rename(backup)
            log.warning(f"{parent} 是文件, 已重命名为 {backup}")
        except Exception as e:  # noqa: BLE001
            log.warning(f"无法重命名阻塞文件 {parent}: {e}")
    parent.mkdir(parents=True, exist_ok=True)


def get_conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    _ensure_parent()
    conn = sqlite3.connect(str(_PATH), check_same_thread=False, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    with _LOCK:
        conn.executescript(_SCHEMA)
        conn.commit()
    _CONN = conn
    return conn


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 新闻
# ---------------------------------------------------------------------------

def upsert_news(items: List[Dict[str, Any]], category: str) -> int:
    if not items:
        return 0
    try:
        conn = get_conn()
    except Exception as e:  # noqa: BLE001
        log.warning(f"db upsert_news 连接失败: {e}")
        return 0
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t = (it.get("time") or "").strip()
        title = (it.get("title") or "").strip()
        if not title:
            continue
        h = hashlib.md5(f"{t}|{title}".encode("utf-8")).hexdigest()
        rows.append((
            time.time(), t, title,
            it.get("summary") or "",
            it.get("source") or "",
            category, h,
        ))
    if not rows:
        return 0
    try:
        with _LOCK:
            cur = conn.executemany(
                "INSERT OR IGNORE INTO news "
                "(ts, event_time, title, summary, source, category, hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            return cur.rowcount or 0
    except Exception as e:  # noqa: BLE001
        log.warning(f"db upsert_news 失败: {e}")
        return 0


def recent_news(category: Optional[str] = None, limit: int = 80, days: int = 7) -> List[Dict[str, Any]]:
    try:
        conn = get_conn()
    except Exception as e:  # noqa: BLE001
        log.warning(f"db recent_news 连接失败: {e}")
        return []
    since = time.time() - days * 86400
    try:
        with _LOCK:
            if category:
                cur = conn.execute(
                    "SELECT event_time AS time, title, summary, source "
                    "FROM news WHERE category = ? AND ts >= ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (category, since, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT event_time AS time, title, summary, source "
                    "FROM news WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                    (since, limit),
                )
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        log.warning(f"db recent_news 失败: {e}")
        return []


# ---------------------------------------------------------------------------
# 大盘日级数据
# ---------------------------------------------------------------------------

def upsert_index_history(histories: Dict[str, pd.DataFrame]) -> int:
    if not histories:
        return 0
    rows = []
    for name, df in histories.items():
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            continue
        for _, row in df.iterrows():
            d_raw = row.get("date")
            if isinstance(d_raw, pd.Timestamp):
                d = d_raw.strftime("%Y-%m-%d")
            else:
                d = str(d_raw)[:10] if d_raw is not None else ""
            if not d:
                continue
            rows.append((
                d, str(name), str(name),
                _safe_float(row.get("close")),
                _safe_float(row.get("change_pct")),
            ))
    if not rows:
        return 0
    try:
        conn = get_conn()
        with _LOCK:
            conn.executemany(
                "INSERT OR REPLACE INTO index_daily "
                "(date, code, name, close, change_pct) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        log.warning(f"db upsert_index_history 失败: {e}")
        return 0


def upsert_north_history(df: pd.DataFrame) -> int:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return 0
    rows = []
    for _, row in df.iterrows():
        d_raw = row.get("date")
        if isinstance(d_raw, pd.Timestamp):
            d = d_raw.strftime("%Y-%m-%d")
        else:
            d = str(d_raw)[:10] if d_raw is not None else ""
        if not d:
            continue
        net = _safe_float(row.get("net")) or _safe_float(row.get("north_money")) or _safe_float(row.get("total"))
        if net is None:
            continue
        rows.append((d, net))
    if not rows:
        return 0
    try:
        conn = get_conn()
        with _LOCK:
            conn.executemany(
                "INSERT OR REPLACE INTO north_daily (date, net) VALUES (?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        log.warning(f"db upsert_north_history 失败: {e}")
        return 0


def upsert_margin_history(df: pd.DataFrame) -> int:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return 0
    rows = []
    for _, row in df.iterrows():
        d_raw = row.get("date")
        if isinstance(d_raw, pd.Timestamp):
            d = d_raw.strftime("%Y-%m-%d")
        else:
            d = str(d_raw)[:10] if d_raw is not None else ""
        if not d:
            continue
        balance = _safe_float(row.get("balance")) or _safe_float(row.get("total"))
        fin = _safe_float(row.get("financing_balance"))
        if balance is None and fin is None:
            continue
        rows.append((d, balance, fin))
    if not rows:
        return 0
    try:
        conn = get_conn()
        with _LOCK:
            conn.executemany(
                "INSERT OR REPLACE INTO margin_daily (date, balance, financing_balance) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        log.warning(f"db upsert_margin_history 失败: {e}")
        return 0


def upsert_valuations(date: str, valuations: Dict[str, Dict[str, Any]]) -> int:
    if not valuations:
        return 0
    rows = []
    for name, v in valuations.items():
        if not isinstance(v, dict):
            continue
        rows.append((
            date, str(name),
            _safe_float(v.get("pe")),
            _safe_float(v.get("pb")),
            _safe_float(v.get("pe_percentile")),
            _safe_float(v.get("pb_percentile")),
        ))
    if not rows:
        return 0
    try:
        conn = get_conn()
        with _LOCK:
            conn.executemany(
                "INSERT OR REPLACE INTO valuation_daily "
                "(date, name, pe, pb, pe_percentile, pb_percentile) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        log.warning(f"db upsert_valuations 失败: {e}")
        return 0


def upsert_breadth_daily(date: str, breadth: Dict[str, Any]) -> bool:
    if not breadth:
        return False
    try:
        conn = get_conn()
        payload = json.dumps(breadth, ensure_ascii=False, default=str)
        with _LOCK:
            conn.execute(
                "INSERT OR REPLACE INTO breadth_daily "
                "(date, up_ratio, limit_up, limit_down, median_change, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    date,
                    _safe_float(breadth.get("up_ratio")),
                    int(breadth["limit_up"]) if isinstance(breadth.get("limit_up"), (int, float)) else None,
                    int(breadth["limit_down"]) if isinstance(breadth.get("limit_down"), (int, float)) else None,
                    _safe_float(breadth.get("median_change")),
                    payload,
                ),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning(f"db upsert_breadth_daily 失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 盘中估算偏差
# ---------------------------------------------------------------------------

def _parse_est_date(est_time: str) -> Optional[str]:
    if not est_time:
        return None
    s = str(est_time).strip()
    if len(s) >= 10 and s[4:5] == "-":
        return s[:10]
    if len(s) >= 5 and s[2:3] == "-":
        return f"{datetime.now().year}-{s[:5]}"
    return None


def record_estimate(code: str, est_pct: float, est_time: str) -> None:
    if not code or not isinstance(est_pct, (int, float)):
        return
    d = _parse_est_date(est_time) or _today()
    try:
        conn = get_conn()
        with _LOCK:
            conn.execute(
                "INSERT OR REPLACE INTO fund_estimate (code, date, est_pct, est_time) VALUES (?, ?, ?, ?)",
                (str(code), d, float(est_pct), est_time or ""),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        log.warning(f"db record_estimate 失败: {e}")


def reconcile_bias(code: str, history_df: pd.DataFrame) -> int:
    if history_df is None or not isinstance(history_df, pd.DataFrame) or history_df.empty:
        return 0
    if "date" not in history_df.columns or "change_pct" not in history_df.columns:
        return 0
    today = _today()
    try:
        conn = get_conn()
        with _LOCK:
            cur = conn.execute("SELECT date FROM fund_bias WHERE code = ?", (str(code),))
            seen = {r["date"] for r in cur.fetchall()}
            cur = conn.execute("SELECT date, est_pct FROM fund_estimate WHERE code = ?", (str(code),))
            estimates = {r["date"]: r["est_pct"] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        log.warning(f"db reconcile_bias 读取失败: {e}")
        return 0

    rows = []
    for _, row in history_df.iterrows():
        d_raw = row.get("date")
        if isinstance(d_raw, pd.Timestamp):
            d = d_raw.strftime("%Y-%m-%d")
        else:
            d = str(d_raw)[:10] if d_raw is not None else ""
        if not d or d in seen or d == today:
            continue
        actual = _safe_float(row.get("change_pct"))
        if actual is None:
            continue
        est = estimates.get(d)
        if est is None:
            continue
        rows.append((str(code), d, float(est), actual, abs(actual - float(est))))
    if not rows:
        return 0
    try:
        with _LOCK:
            conn.executemany(
                "INSERT OR REPLACE INTO fund_bias "
                "(code, date, est_pct, actual_pct, abs_err) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        log.warning(f"db reconcile_bias 写入失败: {e}")
        return 0


def get_bias_stats(code: str, window: int = 20) -> Dict[str, Any]:
    try:
        conn = get_conn()
        with _LOCK:
            cur = conn.execute(
                "SELECT est_pct, actual_pct, abs_err FROM fund_bias "
                "WHERE code = ? ORDER BY date DESC LIMIT ?",
                (str(code), int(window)),
            )
            rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:  # noqa: BLE001
        log.warning(f"db get_bias_stats 失败: {e}")
        return {"n": 0, "confidence": "low"}

    n = len(rows)
    if n == 0:
        return {"n": 0, "confidence": "low"}
    mae = sum((r.get("abs_err") or 0.0) for r in rows) / n
    bias = sum(((r.get("est_pct") or 0.0) - (r.get("actual_pct") or 0.0)) for r in rows) / n
    conf = "high" if n >= 15 else "medium" if n >= 8 else "low"
    last_err = rows[0].get("abs_err")
    return {
        "n": n,
        "mae": round(mae, 3),
        "bias": round(bias, 3),
        "confidence": conf,
        "last_err": round(last_err, 3) if last_err is not None else None,
    }


# ---------------------------------------------------------------------------
# 保留
# ---------------------------------------------------------------------------

def cleanup(days: int = _RETENTION_DAYS) -> Dict[str, int]:
    try:
        conn = get_conn()
    except Exception as e:  # noqa: BLE001
        log.warning(f"db cleanup 连接失败: {e}")
        return {}
    cutoff_ts = time.time() - days * 86400
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    counts: Dict[str, int] = {}
    tasks = [
        ("news",            "ts",   cutoff_ts),
        ("index_daily",     "date", cutoff_date),
        ("north_daily",     "date", cutoff_date),
        ("breadth_daily",   "date", cutoff_date),
        ("valuation_daily", "date", cutoff_date),
        ("margin_daily",    "date", cutoff_date),
        ("fund_estimate",   "date", cutoff_date),
        ("fund_bias",       "date", cutoff_date),
    ]
    try:
        with _LOCK:
            for table, col, val in tasks:
                cur = conn.execute(f"DELETE FROM {table} WHERE {col} < ?", (val,))
                counts[table] = cur.rowcount
            conn.commit()
    except Exception as e:  # noqa: BLE001
        log.warning(f"db cleanup 失败: {e}")
    return counts


def vacuum_if_due() -> bool:
    global _LAST_VACUUM
    now = time.time()
    if now - _LAST_VACUUM < _VACUUM_INTERVAL_DAYS * 86400:
        return False
    try:
        conn = get_conn()
        with _LOCK:
            conn.execute("VACUUM;")
        _LAST_VACUUM = now
        log.info("db VACUUM 完成")
        return True
    except Exception as e:  # noqa: BLE001
        log.warning(f"db VACUUM 失败: {e}")
        return False
