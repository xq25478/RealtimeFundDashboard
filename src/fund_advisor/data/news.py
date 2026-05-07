"""新闻与政策抓取"""

import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import pandas as pd

from fund_advisor.utils.logger import get_logger

log = get_logger(__name__)


def _safe_import_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError as e:
        raise ImportError("未安装 akshare，请先运行: pip install akshare") from e


def get_finance_news(limit: int = 20) -> List[Dict[str, str]]:
    """获取最新财经新闻（来自东方财富 7x24）。"""
    ak = _safe_import_akshare()
    try:
        df = ak.stock_info_global_em()
    except Exception as e:
        log.warning(f"获取财经新闻失败: {e}")
        return []

    if df is None or df.empty:
        return []

    rename = {
        "标题": "title",
        "摘要": "summary",
        "发布时间": "time",
        "链接": "url",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    items: List[Dict[str, str]] = []
    for _, row in df.head(limit).iterrows():
        items.append({
            "title": str(row.get("title", "")),
            "summary": str(row.get("summary", "")),
            "time": str(row.get("time", "")),
            "url": str(row.get("url", "")),
        })
    return items


def get_policy_news(limit: int = 10) -> List[Dict[str, str]]:
    """获取政策面相关新闻（按关键词过滤）"""
    keywords = (
        "央行", "证监会", "国务院", "发改委", "财政部", "银保监", "降准", "降息",
        "MLF", "LPR", "刺激", "稳增长", "宏观", "政策", "新政",
    )

    news = get_finance_news(limit=200)
    matched: List[Dict[str, str]] = []
    for item in news:
        text = item.get("title", "") + item.get("summary", "")
        if any(k in text for k in keywords):
            matched.append(item)
        if len(matched) >= limit:
            break
    return matched


# ---- 时间过滤 / 近 N 日抓取 ------------------------------------------------

NEWS_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y年%m月%d日 %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d %H:%M",
)


def _parse_news_time(s) -> Optional[datetime]:
    """解析新闻时间字符串, 支持多种格式与"HH:MM"/"MM-DD HH:MM"简写"""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s.lower() in ("nan", "none"):
        return None

    for fmt in NEWS_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", s):
        try:
            t = datetime.strptime(s.split(":", 2)[0] + ":" + s.split(":")[1], "%H:%M").time()
            return datetime.combine(datetime.now().date(), t)
        except ValueError:
            return None

    m = re.fullmatch(r"(\d{1,2})[-/月](\d{1,2})[日]?\s+(\d{1,2}):(\d{2})", s)
    if m:
        try:
            mo, da, hh, mm = map(int, m.groups())
            return datetime(datetime.now().year, mo, da, hh, mm)
        except ValueError:
            return None

    return None


POLICY_KEYWORDS = (
    "央行", "证监会", "国务院", "发改委", "财政部", "银保监会", "金融监管总局",
    "降准", "降息", "MLF", "LPR", "OMO", "PSL", "再贷款",
    "刺激", "稳增长", "宏观", "政策", "新政", "扩内需", "促消费", "稳楼市",
    "改革", "试点", "开放", "稳定", "支持", "扶持", "补贴", "减税", "降费",
    "调控", "整顿", "限制", "禁止", "约谈", "监管", "处罚",
    "国常会", "政治局", "三中全会", "中央经济工作会议", "两会", "财新",
    "证监", "外汇局", "工信部", "科技部", "商务部",
)


def get_recent_news(days: int = 7, fetch: int = 300) -> List[Dict[str, str]]:
    """近 N 天财经新闻 (东财 7x24); 已按时间倒序、按标题去重.

    无法解析时间的条目会被丢弃(避免污染时间窗口).
    """
    items = get_finance_news(limit=fetch)
    cutoff = datetime.now() - timedelta(days=days)

    seen: set = set()
    out: List[Dict[str, str]] = []
    for item in items:
        title = (item.get("title") or "").strip()
        if not title or title in seen:
            continue
        dt = _parse_news_time(item.get("time", ""))
        if dt is None or dt < cutoff:
            continue
        seen.add(title)
        rec = dict(item)
        rec["_ts"] = dt.isoformat(sep=" ", timespec="minutes")
        out.append(rec)

    out.sort(key=lambda x: x.get("_ts", ""), reverse=True)
    return out


def get_recent_policy_news(days: int = 7, fetch: int = 400) -> List[Dict[str, str]]:
    """近 N 天政策类新闻, 命中关键词记入字段 _keywords"""
    items = get_recent_news(days=days, fetch=fetch)
    matched: List[Dict[str, str]] = []
    for item in items:
        text = (item.get("title", "") or "") + " " + (item.get("summary", "") or "")
        hits = [k for k in POLICY_KEYWORDS if k in text]
        if hits:
            rec = dict(item)
            rec["_keywords"] = "|".join(hits[:5])
            matched.append(rec)
    return matched
