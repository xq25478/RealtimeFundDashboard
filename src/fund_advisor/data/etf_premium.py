"""场内 ETF 折溢价率

数据源: akshare.fund_etf_spot_em()
适用于场内交易的 ETF (510xxx / 159xxx / 561xxx 等)
场外联接基金不适用.
"""

from typing import Dict, List, Optional

import pandas as pd

from fund_advisor.utils.logger import get_logger

log = get_logger(__name__)


# 仅形如 5xxxxx / 1xxxxx 的场内代码会被命中
def _is_etf_code(code: str) -> bool:
    s = str(code).strip().zfill(6)
    return s.isdigit() and (s.startswith(("51", "52", "56", "58", "15", "16")))


def get_etf_premium(codes: Optional[List[str]] = None) -> Dict[str, Dict]:
    """获取指定 ETF 的当日折溢价 / 实时价 / 涨跌幅.

    返回: { code: {name, price, change_pct, premium_pct, iopv} }
    缺失字段为 None. 全网失败返回 {}.
    """
    try:
        import akshare as ak
    except ImportError:
        return {}

    try:
        df = ak.fund_etf_spot_em()
    except Exception as e:
        log.debug(f"fund_etf_spot_em 失败: {e}")
        return {}

    if df is None or df.empty:
        return {}

    rename = {
        "代码": "code",
        "名称": "name",
        "最新价": "price",
        "涨跌幅": "change_pct",
        "折价率": "discount_pct",
        "溢价率": "premium_pct",
        "IOPV实时估值": "iopv",
        "成交额": "amount",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "code" not in df.columns:
        return {}

    df["code"] = df["code"].astype(str).str.zfill(6)

    if codes:
        targets = {str(c).strip().zfill(6) for c in codes if _is_etf_code(c)}
        if not targets:
            return {}
        df = df[df["code"].isin(targets)]

    out: Dict[str, Dict] = {}
    for _, row in df.iterrows():
        c = row.get("code")
        if not c:
            continue
        # premium_pct: 优先用 溢价率 列; 否则用 折价率 取负; 部分行情接口字段名混用
        premium = row.get("premium_pct")
        if not isinstance(premium, (int, float)) or pd.isna(premium):
            disc = row.get("discount_pct")
            if isinstance(disc, (int, float)) and not pd.isna(disc):
                premium = -float(disc)

        def _f(v):
            return float(v) if isinstance(v, (int, float)) and not pd.isna(v) else None

        out[c] = {
            "name": str(row.get("name", "")) or None,
            "price": _f(row.get("price")),
            "change_pct": _f(row.get("change_pct")),
            "premium_pct": _f(premium),
            "iopv": _f(row.get("iopv")),
        }
    return out
