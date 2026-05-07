"""技术指标分析（均线 / MACD / RSI / 动量）

仅使用 numpy + pandas 实现，避免依赖 TA-Lib / pandas-ta 编译问题。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class TechnicalSignal:
    """技术面信号"""
    score: float                     # -100 ~ +100，正为看多
    trend: str                        # "上升" / "下降" / "震荡"
    ma_signal: str                    # 多头 / 空头 / 纠缠
    macd_signal: str                  # 金叉 / 死叉 / 中性
    rsi_value: float                  # 0 ~ 100
    rsi_signal: str                   # 超买 / 超卖 / 正常
    volatility: float                 # 年化波动率 %
    max_drawdown: float               # 区间最大回撤 %
    details: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(50)


def _macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    dif = ema_fast - ema_slow
    dea = _ema(dif, signal)
    hist = (dif - dea) * 2
    return dif, dea, hist


def _max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    peak = nav.cummax()
    dd = (nav - peak) / peak
    return float(dd.min() * 100)


def analyze_technical(
    df: pd.DataFrame,
    price_col: str = "close",
) -> TechnicalSignal:
    """对给定价格序列做技术面分析。

    Args:
        df: 必须包含 price_col 列（基金净值或指数收盘价）
        price_col: 价格列名（基金用 "nav"，指数用 "close"）
    """
    if df is None or df.empty or price_col not in df.columns:
        return TechnicalSignal(
            score=0, trend="未知", ma_signal="未知", macd_signal="未知",
            rsi_value=50, rsi_signal="未知", volatility=0, max_drawdown=0,
            warnings=["数据缺失，无法计算技术指标"],
        )

    price = df[price_col].astype(float).dropna()
    if len(price) < 30:
        return TechnicalSignal(
            score=0, trend="样本不足", ma_signal="未知", macd_signal="未知",
            rsi_value=50, rsi_signal="未知", volatility=0, max_drawdown=0,
            warnings=[f"样本仅 {len(price)} 条，至少需要 30 条"],
        )

    ma5 = price.rolling(5).mean()
    ma20 = price.rolling(20).mean()
    ma60 = price.rolling(60).mean() if len(price) >= 60 else price.rolling(len(price)).mean()

    latest_price = float(price.iloc[-1])
    latest_ma5 = float(ma5.iloc[-1])
    latest_ma20 = float(ma20.iloc[-1])
    latest_ma60 = float(ma60.iloc[-1])

    if latest_ma5 > latest_ma20 > latest_ma60:
        ma_signal, ma_score = "多头排列", 20
    elif latest_ma5 < latest_ma20 < latest_ma60:
        ma_signal, ma_score = "空头排列", -20
    elif latest_price > latest_ma20:
        ma_signal, ma_score = "站上20日线", 10
    elif latest_price < latest_ma20:
        ma_signal, ma_score = "跌破20日线", -10
    else:
        ma_signal, ma_score = "均线纠缠", 0

    dif, dea, hist = _macd(price)
    hist_now = float(hist.iloc[-1])
    hist_prev = float(hist.iloc[-2]) if len(hist) >= 2 else 0
    if hist_prev <= 0 < hist_now:
        macd_signal, macd_score = "金叉（买入信号）", 15
    elif hist_prev >= 0 > hist_now:
        macd_signal, macd_score = "死叉（卖出信号）", -15
    elif hist_now > 0:
        macd_signal, macd_score = "多头延续", 8
    elif hist_now < 0:
        macd_signal, macd_score = "空头延续", -8
    else:
        macd_signal, macd_score = "中性", 0

    rsi = _rsi(price)
    rsi_now = float(rsi.iloc[-1])
    if rsi_now >= 75:
        rsi_signal, rsi_score = "超买（回调风险）", -10
    elif rsi_now >= 65:
        rsi_signal, rsi_score = "偏强", 5
    elif rsi_now <= 25:
        rsi_signal, rsi_score = "超卖（反弹机会）", 10
    elif rsi_now <= 35:
        rsi_signal, rsi_score = "偏弱", -5
    else:
        rsi_signal, rsi_score = "正常区间", 0

    returns = price.pct_change().dropna()
    volatility = float(returns.std() * np.sqrt(252) * 100) if not returns.empty else 0
    mdd = _max_drawdown(price)

    change_20 = (latest_price / float(price.iloc[-20]) - 1) * 100 if len(price) >= 20 else 0
    if change_20 > 10:
        trend = "强势上升"
    elif change_20 > 3:
        trend = "温和上升"
    elif change_20 < -10:
        trend = "加速下跌"
    elif change_20 < -3:
        trend = "温和下跌"
    else:
        trend = "横盘震荡"

    total_score = ma_score + macd_score + rsi_score
    total_score = max(-100, min(100, total_score))

    warnings = []
    if volatility > 40:
        warnings.append(f"波动率偏高 ({volatility:.1f}%)，注意控制仓位")
    if mdd < -20:
        warnings.append(f"区间最大回撤 {mdd:.1f}%，历史抗跌性一般")
    if rsi_now >= 80:
        warnings.append("RSI 严重超买，短线过热")

    return TechnicalSignal(
        score=total_score,
        trend=trend,
        ma_signal=ma_signal,
        macd_signal=macd_signal,
        rsi_value=rsi_now,
        rsi_signal=rsi_signal,
        volatility=volatility,
        max_drawdown=mdd,
        details={
            "price": latest_price,
            "ma5": latest_ma5,
            "ma20": latest_ma20,
            "ma60": latest_ma60,
            "change_20d": change_20,
            "macd_hist": hist_now,
        },
        warnings=warnings,
    )
