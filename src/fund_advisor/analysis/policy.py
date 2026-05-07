"""政策面解读

基于关键词识别政策强度与方向。
"""

from dataclasses import dataclass, field
from typing import Dict, List


POSITIVE_POLICY = {
    "降准": 15, "降息": 15, "LPR下调": 12, "MLF": 8,
    "减税": 10, "降费": 8, "补贴": 8, "扶持": 6,
    "稳增长": 10, "刺激": 10, "扩内需": 8, "新基建": 8,
    "放开": 8, "放宽": 6, "鼓励": 5, "支持": 4,
    "回购": 6, "增持": 6, "注资": 8,
}

NEGATIVE_POLICY = {
    "加息": -15, "紧缩": -12, "收紧": -10,
    "限制": -8, "禁止": -10, "整顿": -8, "处罚": -6,
    "反垄断": -6, "调查": -5, "约谈": -5,
    "提高存款准备金": -15, "减持": -6,
}


@dataclass
class PolicySignal:
    """政策信号"""
    score: float
    direction: str                         # "利好" / "利空" / "中性"
    hits: List[Dict[str, str]] = field(default_factory=list)
    summary: str = ""


def analyze_policy(news_items: List[Dict[str, str]]) -> PolicySignal:
    if not news_items:
        return PolicySignal(score=0, direction="中性", summary="未获取到政策类新闻")

    total = 0.0
    hits: List[Dict[str, str]] = []

    for item in news_items:
        text = item.get("title", "") + " " + item.get("summary", "")
        if not text.strip():
            continue

        matched_pos = [(k, v) for k, v in POSITIVE_POLICY.items() if k in text]
        matched_neg = [(k, v) for k, v in NEGATIVE_POLICY.items() if k in text]

        if not matched_pos and not matched_neg:
            continue

        delta = sum(v for _, v in matched_pos) + sum(v for _, v in matched_neg)
        total += delta

        keywords = ",".join(k for k, _ in matched_pos + matched_neg)
        hits.append({
            "title": item.get("title", "")[:70],
            "keywords": keywords,
            "score": delta,
            "time": item.get("time", ""),
        })

    total = max(-100, min(100, total))
    if total >= 15:
        direction = "利好"
        summary = f"政策面偏暖，正面信号 {sum(1 for h in hits if h['score'] > 0)} 条"
    elif total <= -15:
        direction = "利空"
        summary = f"政策面偏紧，负面信号 {sum(1 for h in hits if h['score'] < 0)} 条"
    else:
        direction = "中性"
        summary = "政策面无明显倾向"

    hits.sort(key=lambda x: abs(x["score"]), reverse=True)

    return PolicySignal(
        score=total,
        direction=direction,
        hits=hits[:8],
        summary=summary,
    )
