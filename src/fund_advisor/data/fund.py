"""基金数据获取

主要数据源：
  - 天天基金网实时估值 JSONP 接口（无需 SDK、无限流）
  - akshare（可选增强：历史净值、评级、规模等）
"""

import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd
import requests

from fund_advisor.utils.logger import get_logger

log = get_logger(__name__)


REALTIME_URL = "http://fundgz.1234567.com.cn/js/{code}.js"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36"
    ),
    "Referer": "http://fund.eastmoney.com/",
}


def _safe_import_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError:
        return None


def get_fund_realtime(code: str, timeout: int = 8) -> Dict:
    """通过天天基金 JSONP 接口获取基金实时估值。

    返回字段:
        code          基金代码
        name          基金名称
        last_nav      上一交易日单位净值
        last_nav_date 单位净值日期
        estimate_nav  盘中估算净值
        estimate_pct  盘中估算涨跌幅 %
        estimate_time 估算时间
    """
    code = str(code).zfill(6)
    try:
        resp = requests.get(
            REALTIME_URL.format(code=code),
            headers=REQUEST_HEADERS,
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.text.strip()
    except Exception as e:
        log.warning(f"获取基金 {code} 实时估值失败: {e}")
        return {"code": code}

    m = re.search(r"jsonpgz\((.*)\)", text, re.DOTALL)
    if not m:
        return {"code": code}

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {"code": code}

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "code": data.get("fundcode", code),
        "name": data.get("name", ""),
        "last_nav": _num(data.get("dwjz")),
        "last_nav_date": data.get("jzrq", ""),
        "estimate_nav": _num(data.get("gsz")),
        "estimate_pct": _num(data.get("gszzl")),
        "estimate_time": data.get("gztime", ""),
    }


def get_fund_estimate(code: str) -> Dict:
    """别名保留"""
    return get_fund_realtime(code)


def get_fund_history(code: str, days: int = 180) -> pd.DataFrame:
    """获取基金历史净值（优先 akshare，失败回落到天天基金接口）。

    返回 DataFrame 列: date, nav, accumulate_nav, change_pct
    """
    code = str(code).zfill(6)

    ak = _safe_import_akshare()
    if ak is not None:
        try:
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df is not None and not df.empty:
                rename = {
                    "净值日期": "date",
                    "单位净值": "nav",
                    "日增长率": "change_pct",
                }
                df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                if "change_pct" in df.columns:
                    df["change_pct"] = pd.to_numeric(df["change_pct"], errors="coerce")
                if "nav" in df.columns:
                    df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
                return df.sort_values("date").tail(days).reset_index(drop=True)
        except Exception as e:
            log.debug(f"akshare 获取基金历史失败，回落到 HTTP: {e}")

    return _fallback_fund_history(code, days)


def _fallback_fund_history(code: str, days: int) -> pd.DataFrame:
    """天天基金净值走势接口（F10 页面）"""
    end = datetime.now().date()
    start = end - timedelta(days=int(days * 1.6) + 30)
    url = (
        "http://api.fund.eastmoney.com/f10/lsjz"
        f"?fundCode={code}&pageIndex=1&pageSize={days + 20}"
        f"&startDate={start.isoformat()}&endDate={end.isoformat()}"
    )
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=8)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        log.warning(f"获取基金 {code} 历史净值失败: {e}")
        return pd.DataFrame()

    items = (payload.get("Data") or {}).get("LSJZList") or []
    if not items:
        return pd.DataFrame()

    df = pd.DataFrame(items)
    col_map = {"FSRQ": "date", "DWJZ": "nav", "LJJZ": "accumulate_nav", "JZZZL": "change_pct"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ("nav", "accumulate_nav", "change_pct"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").tail(days).reset_index(drop=True)
    return df
