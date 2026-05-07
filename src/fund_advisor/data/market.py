"""大盘数据获取（基于 akshare）"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from fund_advisor.utils.logger import get_logger

log = get_logger(__name__)


INDEX_NAME_MAP = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000016": "上证50",
    "sh000688": "科创50",
}


def _safe_import_akshare():
    """延迟导入 akshare，缺失时给出友好提示"""
    try:
        import akshare as ak
        return ak
    except ImportError as e:
        raise ImportError(
            "未安装 akshare，请先运行: pip install akshare"
        ) from e


def get_index_realtime(symbols: Optional[List[str]] = None) -> pd.DataFrame:
    """获取核心指数实时行情。

    优先用新浪接口（覆盖上证、深证、创业板、沪深300、中证500等核心指数），
    失败时回落到东财"沪深重要指数"。
    返回字段: code, name, price, change_pct, change, volume, amount
    """
    ak = _safe_import_akshare()
    df = pd.DataFrame()

    try:
        df = ak.stock_zh_index_spot_sina()
    except Exception as e:
        log.debug(f"新浪指数接口失败，尝试东财: {e}")

    if df is None or df.empty:
        try:
            df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
        except Exception as e:
            log.warning(f"获取指数实时数据失败: {e}")
            return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    rename = {
        "代码": "code",
        "名称": "name",
        "最新价": "price",
        "涨跌幅": "change_pct",
        "涨跌额": "change",
        "成交量": "volume",
        "成交额": "amount",
        "昨收": "prev_close",
        "今开": "open",
        "最高": "high",
        "最低": "low",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    if symbols:
        wanted_names = {INDEX_NAME_MAP.get(s) for s in symbols} | set(symbols)
        wanted_names.discard(None)
        df = df[df["name"].isin(wanted_names) | df["code"].isin(symbols)]

    return df.reset_index(drop=True)


def get_index_data(
    symbol: str = "sh000001",
    start: Optional[str] = None,
    end: Optional[str] = None,
    period: str = "daily",
) -> pd.DataFrame:
    """获取指数历史 K 线数据。

    Args:
        symbol: 形如 sh000001 / sz399006
        start: YYYYMMDD
        end: YYYYMMDD
        period: daily / weekly / monthly
    """
    ak = _safe_import_akshare()
    end = end or datetime.now().strftime("%Y%m%d")
    start = start or (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")

    try:
        df = ak.index_zh_a_hist(
            symbol=symbol[2:] if symbol.startswith(("sh", "sz")) else symbol,
            period=period,
            start_date=start,
            end_date=end,
        )
    except Exception as e:
        log.warning(f"获取指数 {symbol} 历史数据失败: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    rename = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "振幅": "amplitude",
        "涨跌幅": "change_pct",
        "涨跌额": "change",
        "换手率": "turnover",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
    return df


def get_north_money() -> Dict[str, float]:
    """获取北向资金当日净流入概况（亿元）"""
    ak = _safe_import_akshare()
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
    except Exception as e:
        log.warning(f"获取北向资金失败: {e}")
        return {}

    if df is None or df.empty:
        return {}

    type_keys = ("类型", "板块", "类别", "name", "Type", "板块名称")
    flow_keys = (
        "当日资金流入", "当日净买额", "净流入", "成交净买额",
        "今日净流入", "今日资金流入", "净买额",
    )

    type_col = next((c for c in type_keys if c in df.columns), None)
    flow_col = next((c for c in flow_keys if c in df.columns), None)

    result: Dict[str, float] = {}
    if type_col and flow_col:
        for _, row in df.iterrows():
            try:
                name = str(row[type_col]).strip()
                val = row[flow_col]
                if name == "" or name.lower() == "nan":
                    continue
                if pd.isna(val):
                    continue
                result[name] = float(val)
            except (ValueError, TypeError):
                continue
        return result

    # 兜底: 老路径
    try:
        for _, row in df.iterrows():
            name = str(row.get("类型", row.get("板块", "")))
            net = row.get("当日资金流入") or row.get("净流入") or row.get("成交净买额")
            if net is None:
                continue
            try:
                result[name] = float(net)
            except (ValueError, TypeError):
                continue
    except Exception as e:
        log.debug(f"解析北向资金数据出错: {e}")

    return result


def get_north_money_history(days: int = 10) -> pd.DataFrame:
    """北向资金近 N 日净流入历史（亿元）.

    多端点回落, 任何一个返回有效数据即采用.
    返回列: date, north_net_flow（单位: 亿元）
    """
    ak = _safe_import_akshare()

    df = pd.DataFrame()
    candidates = [
        ("stock_hsgt_north_net_flow_in_em", {"symbol": "北上"}),
        ("stock_hsgt_hist_em", {"symbol": "北向资金"}),
        ("stock_hsgt_hist_em", {"symbol": "沪股通"}),
    ]
    for fname, kwargs in candidates:
        fn = getattr(ak, fname, None)
        if fn is None:
            continue
        try:
            df = fn(**kwargs)
            if df is not None and not df.empty:
                break
        except Exception as e:
            log.debug(f"{fname}({kwargs}) 调用失败: {e}")
            continue

    if df is None or df.empty:
        return pd.DataFrame()

    date_cols = ("date", "日期", "trade_date")
    flow_cols = (
        "value", "当日成交净买额", "当日资金流入", "成交净买额",
        "净流入", "north_net_flow", "北向", "北上",
    )
    date_col = next((c for c in date_cols if c in df.columns), None)
    flow_col = next((c for c in flow_cols if c in df.columns), None)
    if not date_col or not flow_col:
        log.debug(f"北向资金历史列名异常: {df.columns.tolist()}")
        return pd.DataFrame()

    out = pd.DataFrame({
        "date": pd.to_datetime(df[date_col], errors="coerce"),
        "north_net_flow": pd.to_numeric(df[flow_col], errors="coerce"),
    }).dropna()
    return out.sort_values("date").tail(days).reset_index(drop=True)


def get_margin_balance(days: int = 20) -> pd.DataFrame:
    """沪市两融余额近 N 个交易日（亿元）

    返回列: date, financing_balance (融资余额), short_balance (融券余额),
            total_balance (融资融券余额), financing_buy (融资买入额)
    单位均换算为"亿元"
    """
    ak = _safe_import_akshare()
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=int(days * 1.6) + 10)).strftime("%Y%m%d")

    try:
        df = ak.stock_margin_sse(start_date=start, end_date=end)
    except Exception as e:
        log.warning(f"获取两融余额失败: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    rename = {
        "信用交易日期": "date",
        "融资余额": "financing_balance",
        "融券余量金额": "short_balance",
        "融资融券余额": "total_balance",
        "融资买入额": "financing_buy",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    for col in ("financing_balance", "short_balance", "total_balance", "financing_buy"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce") / 1e8  # 元 → 亿元

    df = df.dropna(subset=["date"]).sort_values("date").tail(days).reset_index(drop=True)
    return df


def get_market_breadth() -> Dict:
    """市场宽度：涨停 / 跌停 / 强势股 / 连板数

    返回:
        zt_count       涨停家数
        dt_count       跌停家数
        strong_count   强势股数（含强势 + 涨停）
        max_consecutive 最高连板数
        consecutive_top  连板代表股 [(name, board_count), ...]
    """
    ak = _safe_import_akshare()
    today = datetime.now().strftime("%Y%m%d")
    out: Dict = {
        "zt_count": None,
        "dt_count": None,
        "strong_count": None,
        "max_consecutive": None,
        "consecutive_top": [],
    }

    try:
        df = ak.stock_zt_pool_em(date=today)
        if df is not None and not df.empty:
            out["zt_count"] = int(len(df))
            if "连板数" in df.columns:
                board = pd.to_numeric(df["连板数"], errors="coerce").dropna()
                if not board.empty:
                    out["max_consecutive"] = int(board.max())
                    top = df.sort_values("连板数", ascending=False).head(5)
                    out["consecutive_top"] = [
                        (str(r.get("名称", "")), int(r.get("连板数", 0)))
                        for _, r in top.iterrows()
                    ]
    except Exception as e:
        log.debug(f"涨停池获取失败: {e}")

    try:
        df = ak.stock_zt_pool_dtgc_em(date=today)
        if df is not None and not df.empty:
            out["dt_count"] = int(len(df))
    except Exception as e:
        log.debug(f"跌停池获取失败: {e}")

    try:
        df = ak.stock_zt_pool_strong_em(date=today)
        if df is not None and not df.empty:
            out["strong_count"] = int(len(df))
    except Exception as e:
        log.debug(f"强势股池获取失败: {e}")

    return out


def get_overseas_indices() -> Dict[str, Dict]:
    """海外重要指数昨日 / 最新表现

    返回 { 指数名: {price, change, change_pct} }
    覆盖：纳斯达克、道指、标普500、恒生科技
    """
    ak = _safe_import_akshare()
    out: Dict[str, Dict] = {}

    us_targets = [
        ("纳斯达克", ".IXIC"),
        ("道琼斯", ".DJI"),
        ("标普500", ".INX"),
    ]
    for name, sym in us_targets:
        try:
            df = ak.index_us_stock_sina(symbol=sym)
            if df is None or df.empty or len(df) < 2:
                continue
            last = df.iloc[-1]
            prev = df.iloc[-2]
            close = float(last["close"])
            prev_close = float(prev["close"])
            chg = close - prev_close
            pct = chg / prev_close * 100 if prev_close else 0
            out[name] = {
                "price": close,
                "change": chg,
                "change_pct": pct,
                "date": str(last.get("date", ""))[:10],
            }
        except Exception as e:
            log.debug(f"获取美股 {name} 失败: {e}")
            continue

    hk_targets = [("恒生科技", "HSTECH"), ("恒生指数", "HSI")]
    for name, sym in hk_targets:
        try:
            df = ak.stock_hk_index_daily_em(symbol=sym)
            if df is None or df.empty or len(df) < 2:
                continue
            last = df.iloc[-1]
            prev = df.iloc[-2]
            price_col = "latest" if "latest" in df.columns else "close"
            close = float(last[price_col])
            prev_close = float(prev[price_col])
            chg = close - prev_close
            pct = chg / prev_close * 100 if prev_close else 0
            out[name] = {
                "price": close,
                "change": chg,
                "change_pct": pct,
                "date": str(last.get("date", ""))[:10],
            }
        except Exception as e:
            log.debug(f"获取港股 {name} 失败: {e}")
            continue

    return out


def get_sector_flow(top_n: int = 10) -> pd.DataFrame:
    """获取行业板块资金流向 TOP N"""
    ak = _safe_import_akshare()
    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
    except Exception as e:
        log.warning(f"获取板块资金流向失败: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    rename = {
        "名称": "sector",
        "今日涨跌幅": "change_pct",
        "今日主力净流入-净额": "main_net_flow",
        "今日主力净流入-净占比": "main_net_pct",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "main_net_flow" in df.columns:
        df = df.sort_values("main_net_flow", ascending=False)
    return df.head(top_n).reset_index(drop=True)
