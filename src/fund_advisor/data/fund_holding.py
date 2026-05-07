"""基金穿透数据：前十大重仓股、行业配置、重仓股当日涨跌归因

数据源：akshare (东财基金 F10)
披露口径：季报，最新一期可能滞后 1-3 个月。
"""

from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from fund_advisor.utils.logger import get_logger

log = get_logger(__name__)


def _safe_import_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError:
        return None


def _latest_year_candidates() -> List[str]:
    """披露季报年份候选：当前年优先，往前找两年"""
    y = datetime.now().year
    return [str(y), str(y - 1), str(y - 2)]


def get_fund_top_holdings(code: str, top_n: int = 10) -> pd.DataFrame:
    """基金最近一期前十大重仓股。

    返回列: rank, stock_code, stock_name, weight (占净值比，%), shares (万股), market_value (万元), quarter
    """
    ak = _safe_import_akshare()
    if ak is None:
        return pd.DataFrame()

    code = str(code).zfill(6)
    df = pd.DataFrame()
    for year in _latest_year_candidates():
        try:
            tmp = ak.fund_portfolio_hold_em(symbol=code, date=year)
            if tmp is not None and not tmp.empty:
                df = tmp
                break
        except Exception as e:
            log.debug(f"fund_portfolio_hold_em({code},{year}) failed: {e}")
            continue

    if df.empty:
        return pd.DataFrame()

    rename = {
        "序号": "rank",
        "股票代码": "stock_code",
        "股票名称": "stock_name",
        "占净值比例": "weight",
        "持股数": "shares",
        "持仓市值": "market_value",
        "季度": "quarter",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    if "quarter" in df.columns and not df["quarter"].empty:
        latest_q = df["quarter"].iloc[0]
        df = df[df["quarter"] == latest_q]

    for col in ("weight", "shares", "market_value"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "stock_code" in df.columns:
        df["stock_code"] = df["stock_code"].astype(str).str.zfill(6)

    return df.head(top_n).reset_index(drop=True)


def get_fund_industry_allocation(code: str, top_n: int = 6) -> pd.DataFrame:
    """基金最近一期行业配置（按占净值比）

    返回列: industry, weight (%), market_value (万元), report_date
    """
    ak = _safe_import_akshare()
    if ak is None:
        return pd.DataFrame()

    code = str(code).zfill(6)
    df = pd.DataFrame()
    for year in _latest_year_candidates():
        try:
            tmp = ak.fund_portfolio_industry_allocation_em(symbol=code, date=year)
            if tmp is not None and not tmp.empty:
                df = tmp
                break
        except Exception as e:
            log.debug(f"fund_portfolio_industry_allocation_em({code},{year}) failed: {e}")
            continue

    if df.empty:
        return pd.DataFrame()

    rename = {
        "行业类别": "industry",
        "占净值比例": "weight",
        "市值": "market_value",
        "截止时间": "report_date",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    if "report_date" in df.columns and not df["report_date"].empty:
        latest_d = df["report_date"].iloc[0]
        df = df[df["report_date"] == latest_d]

    for col in ("weight", "market_value"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "weight" in df.columns:
        df = df.sort_values("weight", ascending=False)
    return df.head(top_n).reset_index(drop=True)


def get_stock_realtime_quote(stock_code: str) -> Dict:
    """单只 A 股当日实时快照（用于重仓股涨跌归因）

    返回字段: code, price, change_pct, change, turnover, volume_ratio
    """
    ak = _safe_import_akshare()
    if ak is None:
        return {}

    code = str(stock_code).zfill(6)
    try:
        df = ak.stock_bid_ask_em(symbol=code)
    except Exception as e:
        log.debug(f"stock_bid_ask_em({code}) failed: {e}")
        return {}

    if df is None or df.empty or "item" not in df.columns:
        return {}

    kv = dict(zip(df["item"], df["value"]))

    def _f(key):
        v = kv.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "code": code,
        "price": _f("最新"),
        "change_pct": _f("涨幅"),
        "change": _f("涨跌"),
        "turnover": _f("换手"),
        "volume_ratio": _f("量比"),
        "high": _f("最高"),
        "low": _f("最低"),
    }


def attribute_fund_today(holdings: pd.DataFrame) -> List[Dict]:
    """对前十大重仓股逐一查询当日涨跌，返回归因列表（按权重排序）

    返回每行: stock_code, stock_name, weight, change_pct, contribution (=weight*change_pct/100, %)
    """
    if holdings is None or holdings.empty:
        return []

    rows: List[Dict] = []
    for _, h in holdings.iterrows():
        sc = h.get("stock_code")
        if not sc:
            continue
        quote = get_stock_realtime_quote(sc)
        change_pct = quote.get("change_pct")
        weight = h.get("weight")
        contrib = None
        if isinstance(change_pct, (int, float)) and isinstance(weight, (int, float)):
            contrib = weight * change_pct / 100.0
        rows.append({
            "stock_code": sc,
            "stock_name": h.get("stock_name", ""),
            "weight": weight,
            "change_pct": change_pct,
            "price": quote.get("price"),
            "turnover": quote.get("turnover"),
            "contribution": contrib,
        })
    return rows
