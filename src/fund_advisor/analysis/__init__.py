"""分析模块"""

from fund_advisor.analysis.technical import analyze_technical, TechnicalSignal
from fund_advisor.analysis.sentiment import analyze_sentiment, SentimentResult
from fund_advisor.analysis.policy import analyze_policy, PolicySignal
from fund_advisor.analysis.decision import make_decision, Decision

__all__ = [
    "analyze_technical",
    "TechnicalSignal",
    "analyze_sentiment",
    "SentimentResult",
    "analyze_policy",
    "PolicySignal",
    "make_decision",
    "Decision",
]
