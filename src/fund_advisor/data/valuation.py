"""指数估值分位：PE/PB 的 5 年 + 10 年历史分位

数据源：akshare 乐估 (stock_index_pe_lg / stock_market_pe_lg / stock_market_pb_lg)
"""

from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd

from fund_advisor.utils.logger import get_logger

log = get_logger(__name__)


def _safe_import_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError:
        return None


INDEX_PE_SYMBOL_MAP = {
    "上证50": ("index", "上证50"),
    "沪深300": ("index", "沪深300"),
    "上证380": ("index", "上证380"),
    "上证指数": ("market", "上证"),
    "深证成指": ("market", "深证"),
    "创业板指": ("market", "创业板"),
}


def _percentile(series: pd.Series, value: float) -> Optional[float]:
    s = series.dropna()
    if s.empty or value is None:
        return None
    return float((s <= value).sum()) / len(s) * 100.0


def _fetch_pe_series(ak, name: str) -> pd.DataFrame:
    """获取指定指数的 PE 时间序列，列: date, pe"""
    kind, sym = INDEX_PE_SYMBOL_MAP.get(name, (None, None))
    if kind is None:
        return pd.DataFrame()

    try:
        if kind == "index":
            df = ak.stock_index_pe_lg(symbol=sym)
            pe_col = "滚动市盈率" if "滚动市盈率" in df.columns else "静态市盈率"
        else:
            df = ak.stock_market_pe_lg(symbol=sym)
            pe_col = "平均市盈率"
    except Exception as e:
        log.debug(f"PE fetch failed for {name}: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"日期": "date", pe_col: "pe"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["pe"] = pd.to_numeric(df["pe"], errors="coerce")
    return df.dropna(subset=["date", "pe"]).sort_values("date").reset_index(drop=True)


def _fetch_pb_series(ak, name: str) -> pd.DataFrame:
    """仅"市场级"有 PB 历史 (stock_market_pb_lg)。指数级此接口不覆盖。"""
    kind, sym = INDEX_PE_SYMBOL_MAP.get(name, (None, None))
    if kind != "market":
        return pd.DataFrame()
    try:
        df = ak.stock_market_pb_lg(symbol=sym)
    except Exception as e:
        log.debug(f"PB fetch failed for {name}: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    pb_col = "市净率" if "市净率" in df.columns else None
    if pb_col is None:
        return pd.DataFrame()

    df = df.rename(columns={"日期": "date", pb_col: "pb"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["pb"] = pd.to_numeric(df["pb"], errors="coerce")
    return df.dropna(subset=["date", "pb"]).sort_values("date").reset_index(drop=True)


def get_index_valuation(name: str) -> Dict:
    """计算一个指数 / 市场的 PE/PB 当前值和 5 年 / 10 年分位。

    返回:
        {
            "name": 名称,
            "pe": 最新PE,
            "pe_pct_5y": 5年分位(%),
            "pe_pct_10y": 10年分位(%),
            "pb": 最新PB (可缺),
            "pb_pct_5y": ...,
            "pb_pct_10y": ...,
            "date": 数据日期,
        }
    缺失的指标置为 None。
    """
    ak = _safe_import_akshare()
    if ak is None:
        return {}

    result: Dict = {"name": name}
    now = datetime.now()
    cutoff_5y = now - timedelta(days=365 * 5)
    cutoff_10y = now - timedelta(days=365 * 10)

    pe_df = _fetch_pe_series(ak, name)
    if not pe_df.empty:
        latest = pe_df.iloc[-1]
        result["pe"] = float(latest["pe"])
        result["date"] = latest["date"].strftime("%Y-%m-%d")
        result["pe_pct_5y"] = _percentile(pe_df[pe_df["date"] >= cutoff_5y]["pe"], result["pe"])
        result["pe_pct_10y"] = _percentile(pe_df[pe_df["date"] >= cutoff_10y]["pe"], result["pe"])

    pb_df = _fetch_pb_series(ak, name)
    if not pb_df.empty:
        latest = pb_df.iloc[-1]
        result["pb"] = float(latest["pb"])
        result.setdefault("date", latest["date"].strftime("%Y-%m-%d"))
        result["pb_pct_5y"] = _percentile(pb_df[pb_df["date"] >= cutoff_5y]["pb"], result["pb"])
        result["pb_pct_10y"] = _percentile(pb_df[pb_df["date"] >= cutoff_10y]["pb"], result["pb"])

    return result


def get_valuations(names: Optional[list] = None) -> Dict[str, Dict]:
    """批量获取估值分位"""
    names = names or ["沪深300", "上证50", "上证指数", "深证成指", "创业板指"]
    out: Dict[str, Dict] = {}
    for n in names:
        try:
            v = get_index_valuation(n)
            if v and (v.get("pe") is not None or v.get("pb") is not None):
                out[n] = v
        except Exception as e:
            log.debug(f"valuation {n} failed: {e}")
            continue
    return out
