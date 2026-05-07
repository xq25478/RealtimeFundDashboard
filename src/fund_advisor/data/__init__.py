"""数据获取模块"""

from fund_advisor.data.market import (
    get_index_data,
    get_index_realtime,
    get_north_money,
    get_sector_flow,
)
from fund_advisor.data.fund import (
    get_fund_realtime,
    get_fund_history,
    get_fund_estimate,
)
from fund_advisor.data.news import get_finance_news, get_policy_news

__all__ = [
    "get_index_data",
    "get_index_realtime",
    "get_north_money",
    "get_sector_flow",
    "get_fund_realtime",
    "get_fund_history",
    "get_fund_estimate",
    "get_finance_news",
    "get_policy_news",
]
