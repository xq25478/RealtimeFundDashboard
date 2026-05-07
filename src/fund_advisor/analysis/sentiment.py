"""新闻情绪分析（基于关键词 + SnowNLP 可选增强）

规则：
  - 先扫描利好 / 利空关键词计分
  - SnowNLP 可用时做文本情感补偿
"""

from dataclasses import dataclass, field
from typing import Dict, List


BULLISH_KEYWORDS = {
    "利好", "超预期", "增长", "突破", "创新高", "涨停", "爆发", "回暖",
    "复苏", "反弹", "扩张", "订单", "签约", "量产", "投资", "增持",
    "减税", "降准", "降息", "稳增长", "刺激", "新政", "支持", "扶持",
    "重组", "并购", "分红", "回购", "盈利", "扭亏", "大涨",
}

BEARISH_KEYWORDS = {
    "利空", "不及预期", "下滑", "下降", "跌停", "暴跌", "亏损", "巨亏",
    "违规", "处罚", "退市", "诉讼", "调查", "风险", "警示", "警告",
    "制裁", "加息", "紧缩", "萎缩", "降级", "减持", "裁员", "破产",
    "爆雷", "违约", "下调", "预亏", "停牌", "大跌", "崩盘", "冲突",
}


@dataclass
class SentimentResult:
    """情绪分析结果"""
    score: float                         # -100 ~ +100
    bullish_count: int
    bearish_count: int
    top_bullish: List[str] = field(default_factory=list)
    top_bearish: List[str] = field(default_factory=list)
    summary: str = ""


def _snownlp_sentiment(text: str) -> float:
    """SnowNLP 情感分（0-1，0.5 为中性），缺失则返回 0.5"""
    try:
        from snownlp import SnowNLP
        if not text:
            return 0.5
        return float(SnowNLP(text).sentiments)
    except Exception:
        return 0.5


def analyze_sentiment(news_items: List[Dict[str, str]]) -> SentimentResult:
    """分析一批新闻/消息的整体情绪"""
    if not news_items:
        return SentimentResult(
            score=0, bullish_count=0, bearish_count=0,
            summary="未获取到新闻数据"
        )

    bullish_count = 0
    bearish_count = 0
    top_bullish: List[str] = []
    top_bearish: List[str] = []
    snownlp_total = 0.0

    for item in news_items:
        text = (item.get("title", "") + " " + item.get("summary", "")).strip()
        if not text:
            continue

        hit_bull = sum(1 for k in BULLISH_KEYWORDS if k in text)
        hit_bear = sum(1 for k in BEARISH_KEYWORDS if k in text)

        bullish_count += hit_bull
        bearish_count += hit_bear

        if hit_bull > hit_bear and len(top_bullish) < 5:
            top_bullish.append(item.get("title", "")[:60])
        elif hit_bear > hit_bull and len(top_bearish) < 5:
            top_bearish.append(item.get("title", "")[:60])

        snownlp_total += _snownlp_sentiment(text[:200])

    total_news = len(news_items)
    snownlp_avg = snownlp_total / total_news if total_news else 0.5

    keyword_delta = bullish_count - bearish_count
    keyword_score = max(-60, min(60, keyword_delta * 6))
    snownlp_score = (snownlp_avg - 0.5) * 80
    score = max(-100, min(100, keyword_score + snownlp_score))

    if score >= 30:
        summary = f"市场情绪偏乐观（利好 {bullish_count} 条 vs 利空 {bearish_count} 条）"
    elif score <= -30:
        summary = f"市场情绪偏悲观（利好 {bullish_count} 条 vs 利空 {bearish_count} 条）"
    else:
        summary = f"市场情绪中性（利好 {bullish_count} 条 vs 利空 {bearish_count} 条）"

    return SentimentResult(
        score=score,
        bullish_count=bullish_count,
        bearish_count=bearish_count,
        top_bullish=top_bullish,
        top_bearish=top_bearish,
        summary=summary,
    )
