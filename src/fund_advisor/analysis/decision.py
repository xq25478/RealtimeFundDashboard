"""综合决策引擎（进攻型配置）

多维度加权融合 → 得到最终评分 → 映射到买卖建议。

风格定位：**进攻型** —— 早进场、慢退场、动量因子加权高、估值容忍度高。

权重（v3 进攻型调整）：
  基金自身技术面  35%   (+5  趋势强者越强)
  大盘技术面      15%
  估值分位         5%   (-5  贵不是禁忌,极端高位才扣)
  消息面情绪       8%   (-2  情绪短期噪音偏多)
  政策面          10%
  板块归因        12%   (+2  跟主力资金走)
  市场宽度         7%   (+2  情绪起来跟着追)
  两融趋势         5%
  北向资金         3%   (-2  反向信号噪音)
  -----
  合计           100%
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Iterable

import pandas as pd

from fund_advisor.analysis.technical import TechnicalSignal
from fund_advisor.analysis.sentiment import SentimentResult
from fund_advisor.analysis.policy import PolicySignal


@dataclass
class Decision:
    """最终决策"""
    fund_code: str
    fund_name: str
    score: float                                 # -100 ~ +100
    action: str                                  # 买入 / 加仓 / 持有 / 减仓 / 卖出
    confidence: str                              # 高 / 中 / 低
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    breakdown: Dict[str, float] = field(default_factory=dict)


# 进攻型阈值：早入场（25 起买入）、宽持有区（-25~5 都耐心）、晚止损（-50 才卖）
ACTION_THRESHOLDS = [
    (25, "买入", "趋势已确认,在交易日尾盘买入"),
    (5, "加仓", "顺势加仓,跟随动量"),
    (-25, "持有", "维持仓位,不轻易交易"),
    (-50, "减仓", "趋势走弱,降低仓位"),
    (-100, "卖出", "趋势破位,清仓离场"),
]


def _map_action(score: float) -> str:
    for threshold, action, _ in ACTION_THRESHOLDS:
        if score >= threshold:
            return action
    return "卖出"


def _confidence(score: float, warnings_count: int) -> str:
    """进攻型：放宽高置信度门槛 —— abs>=35 即可,允许带 1 条警告"""
    abs_score = abs(score)
    if abs_score >= 35 and warnings_count <= 1:
        return "高"
    if abs_score >= 15:
        return "中"
    return "低"


# ---- 因子打分 -----------------------------------------------------------------

def _valuation_score(valuations: Optional[Dict[str, Dict]]) -> Optional[float]:
    """进攻型估值打分曲线（右移 → 对'贵'更宽容）

    只有极端低估才大幅看多,中高估值不显著扣分,> 90% 才视为风险.
    """
    if not valuations:
        return None
    pcts: List[float] = []
    for v in valuations.values():
        p = v.get("pe_pct_10y") or v.get("pe_pct_5y")
        if isinstance(p, (int, float)):
            pcts.append(float(p))
    if not pcts:
        return None
    avg = sum(pcts) / len(pcts)
    if avg < 15:
        return 50      # 极度低估:加分但不夸张(进攻型不靠抄底)
    if avg < 35:
        return 20
    if avg < 60:
        return 5       # 中位数附近:基本中性,不拖累
    if avg < 75:
        return -5      # 偏贵:小幅扣
    if avg < 90:
        return -25     # 贵但仍可参与
    return -55         # > 90% 极度高估:重扣警示


def _breadth_score(breadth: Optional[Dict]) -> Optional[float]:
    """涨停 / 跌停 / 连板 → -60 ~ +70（进攻型放大正向赚钱效应）"""
    if not breadth:
        return None
    z = breadth.get("zt_count")
    d = breadth.get("dt_count")
    mc = breadth.get("max_consecutive")
    if not isinstance(z, (int, float)) or not isinstance(d, (int, float)):
        return None
    base = 0
    if z >= 80 and d <= 30:
        base = 60                    # 强势市场:进攻型加大权重
    elif z >= 50 and d < z:
        base = 35
    elif z >= 30:
        base = 5
    elif z >= d:
        base = -15
    else:
        base = -45
    if isinstance(mc, (int, float)):
        if mc >= 7:
            base += 15               # 高度板出 → 强情绪
        elif mc >= 5:
            base += 5
        elif mc <= 3:
            base -= 10
    return float(max(-60, min(70, base)))


def _margin_score(margin_history: Optional[pd.DataFrame]) -> Optional[float]:
    """两融余额 20 日累计变动 → -60 ~ +60"""
    if margin_history is None or margin_history.empty or "total_balance" not in margin_history.columns:
        return None
    s = margin_history["total_balance"].dropna()
    if len(s) < 2:
        return None
    first = float(s.iloc[0])
    last = float(s.iloc[-1])
    if first == 0:
        return None
    chg = (last - first) / first * 100
    if chg >= 5:
        return 60
    if chg >= 2:
        return 35                    # 进攻型:温和上行已视为正信号
    if chg >= 0:
        return 15
    if chg >= -2:
        return -15
    if chg >= -5:
        return -35
    return -60


def _attribution_score(attribution: Optional[Iterable[Dict]]) -> Optional[float]:
    """重仓股贡献加总(权重×当日涨跌) → -60 ~ +70（进攻型放大正贡献）

    返回 None 表示无归因数据,调用方应回落到 sector_flow.
    """
    if not attribution:
        return None
    total = 0.0
    has = False
    for r in attribution:
        c = r.get("contribution") if isinstance(r, dict) else None
        if isinstance(c, (int, float)):
            total += c
            has = True
    if not has:
        return None
    if total >= 1.5:
        return 70                    # 重仓股齐涨:强加仓信号
    if total >= 0.5:
        return 35
    if total >= -0.5:
        return 0
    if total >= -1.5:
        return -25
    return -55


def _sector_flow_score(sector_net_flow: float) -> float:
    """进攻型:温和净流入也视为正信号"""
    if sector_net_flow > 10:
        return 65
    if sector_net_flow > 2:
        return 30
    if sector_net_flow > -5:
        return -5
    if sector_net_flow > -15:
        return -30
    return -55


def _north_score(north_money: float) -> float:
    if north_money > 30:
        return 60
    if north_money > 10:
        return 25
    if north_money > -10:
        return 0
    if north_money > -30:
        return -25
    return -55


# ---- 主入口 -------------------------------------------------------------------

def make_decision(
    fund_code: str,
    fund_name: str,
    fund_tech: Optional[TechnicalSignal],
    market_tech: Optional[TechnicalSignal],
    sentiment: Optional[SentimentResult],
    policy: Optional[PolicySignal],
    north_money: float = 0.0,
    sector_net_flow: float = 0.0,
    *,
    valuations: Optional[Dict[str, Dict]] = None,
    breadth: Optional[Dict] = None,
    margin_history: Optional[pd.DataFrame] = None,
    attribution: Optional[Iterable[Dict]] = None,
) -> Decision:
    """综合打分 (v3 进攻型)"""
    breakdown: Dict[str, float] = {}
    reasons: List[str] = []
    warnings: List[str] = []

    # 1) 基金技术 35%（进攻型最看重的趋势因子）
    fund_score = fund_tech.score if fund_tech else 0
    breakdown["基金技术面"] = fund_score * 0.35
    if fund_tech:
        reasons.append(f"基金自身：{fund_tech.trend}，{fund_tech.ma_signal}，{fund_tech.macd_signal}")
        warnings.extend(fund_tech.warnings)

    # 2) 大盘技术 15%
    market_score = market_tech.score if market_tech else 0
    breakdown["大盘技术面"] = market_score * 0.15
    if market_tech:
        reasons.append(f"大盘：{market_tech.trend}，{market_tech.ma_signal}")

    # 3) 估值分位 5%（进攻型降权,只在极端时才主导）
    val_raw = _valuation_score(valuations)
    if val_raw is not None:
        breakdown["估值分位"] = val_raw * 0.05
        if val_raw >= 30:
            reasons.append(f"估值偏低（看多加成 {val_raw:+.0f}）")
        elif val_raw <= -40:
            reasons.append(f"估值极度高估（{val_raw:+.0f}）")
            warnings.append("估值分位 > 90%,加仓需控制单次比例")
    else:
        breakdown["估值分位"] = 0

    # 4) 消息面 8%
    sentiment_score = sentiment.score if sentiment else 0
    breakdown["消息面情绪"] = sentiment_score * 0.08
    if sentiment:
        reasons.append(f"消息面：{sentiment.summary}")

    # 5) 政策面 10%
    policy_score = policy.score if policy else 0
    breakdown["政策面"] = policy_score * 0.10
    if policy:
        reasons.append(f"政策面：{policy.summary}")

    # 6) 板块归因 12%（优先用归因, 缺失则板块净流入）
    attr_raw = _attribution_score(attribution)
    if attr_raw is not None:
        breakdown["板块归因"] = attr_raw * 0.12
        reasons.append(f"重仓股归因贡献：{attr_raw:+.0f}")
        if attr_raw >= 35:
            reasons.append("重仓股齐涨,顺势加仓机会")
    else:
        breakdown["板块归因"] = _sector_flow_score(sector_net_flow) * 0.12
        if sector_net_flow != 0:
            reasons.append(f"相关板块资金：{sector_net_flow:+.1f} 亿（无归因数据,回落到板块净流入）")

    # 7) 市场宽度 7%（进攻型加权追涨情绪）
    breadth_raw = _breadth_score(breadth)
    if breadth_raw is not None:
        breakdown["市场宽度"] = breadth_raw * 0.07
        if breadth_raw >= 30:
            reasons.append(f"市场宽度强（{breadth_raw:+.0f}），赚钱效应支持进攻")
        elif breadth_raw <= -30:
            reasons.append(f"市场宽度弱（{breadth_raw:+.0f}）")
            warnings.append("情绪偏弱,优选趋势确立的标的而非全面铺仓")
    else:
        breakdown["市场宽度"] = 0

    # 8) 两融趋势 5%
    margin_raw = _margin_score(margin_history)
    if margin_raw is not None:
        breakdown["两融趋势"] = margin_raw * 0.05
        if margin_raw >= 30:
            reasons.append(f"两融余额上行（{margin_raw:+.0f}），杠杆资金回流")
        elif margin_raw <= -30:
            reasons.append(f"两融余额下行（{margin_raw:+.0f}）")
    else:
        breakdown["两融趋势"] = 0

    # 9) 北向 3%（降权,作为辅助验证）
    breakdown["北向资金"] = _north_score(north_money) * 0.03
    reasons.append(f"北向资金：今日净流入 {north_money:+.1f} 亿")

    total = sum(breakdown.values())
    total = max(-100, min(100, total))

    action = _map_action(total)
    confidence = _confidence(total, len(warnings))

    return Decision(
        fund_code=fund_code,
        fund_name=fund_name,
        score=total,
        action=action,
        confidence=confidence,
        reasons=reasons,
        warnings=warnings,
        breakdown=breakdown,
    )
