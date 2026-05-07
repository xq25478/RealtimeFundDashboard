"""后台分桶刷新线程

dashboard 真正的数据引擎。每个桶一个 daemon 线程, 按不同频率刷新:
  - funds   30s  : 单基金估值 + 技术信号 + 决策
  - market  60s  : 指数实时 + 北向 + 宽度
  - sector 120s  : 板块流向 + 主题映射(合入 market 字典)
  - news   300s  : 新闻 + 情绪 + 政策
  - valuations 12h : PE/PB 分位 + 两融 + 海外 (日级数据)
  - holdings   7d  : 穿透持仓 + 行业 + 归因 + ETF 折溢价

所有异常收进 store.push_error, 保证桶不互相拖垮。
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from fund_advisor.utils.config import Config, load_config
from fund_advisor.utils.logger import get_logger
from fund_advisor.utils import estimate_bias
from fund_advisor.data import db
from fund_advisor.server.state import StateStore, get_store
from fund_advisor.cli import (
    _collect_market,
    _collect_news,
    _summarize_fund,
    _collect_fund_holdings,
    _collect_etf_premium,
    _sector_flow_for_theme,
)
from fund_advisor.analysis.decision import make_decision


log = get_logger("fund_advisor.scheduler")


# 刷新间隔 (秒)
INTERVAL_FUNDS = 30
INTERVAL_MARKET = 60
INTERVAL_SECTOR = 120
INTERVAL_NEWS = 300
INTERVAL_VALUATIONS = 12 * 3600
INTERVAL_HOLDINGS = 7 * 86400

# 冷启动阶段的预热偏移, 避免所有桶在同一秒争抢 akshare
WARMUP_OFFSET = {
    "funds": 0.0,
    "market": 0.5,
    "sector": 1.0,
    "news": 2.0,
    "valuations": 3.0,
    "holdings": 4.0,
}


# ---------------------------------------------------------------------------
# 序列化工具
# ---------------------------------------------------------------------------

def _decision_to_dict(decision) -> Dict[str, Any]:
    return {
        "fund_code": decision.fund_code,
        "fund_name": decision.fund_name,
        "score": decision.score,
        "action": decision.action,
        "confidence": decision.confidence,
        "reasons": list(decision.reasons),
        "warnings": list(decision.warnings),
        "breakdown": dict(decision.breakdown),
    }


def _tech_to_dict(tech) -> Dict[str, Any]:
    if not tech:
        return {}
    return {
        "score": tech.score,
        "trend": getattr(tech, "trend", ""),
        "ma_signal": getattr(tech, "ma_signal", ""),
        "macd_signal": getattr(tech, "macd_signal", ""),
        "rsi_value": getattr(tech, "rsi_value", None),
        "rsi_signal": getattr(tech, "rsi_signal", ""),
        "volatility": getattr(tech, "volatility", None),
        "max_drawdown": getattr(tech, "max_drawdown", None),
        "warnings": list(getattr(tech, "warnings", []) or []),
        "details": dict(getattr(tech, "details", {}) or {}),
    }


def _sentiment_to_dict(s) -> Dict[str, Any]:
    if not s:
        return {}
    return {
        "score": getattr(s, "score", 0),
        "summary": getattr(s, "summary", ""),
        "positive": getattr(s, "positive", 0),
        "negative": getattr(s, "negative", 0),
        "bullish_count": getattr(s, "bullish_count", 0),
        "bearish_count": getattr(s, "bearish_count", 0),
        "details": getattr(s, "details", {}) or {},
    }


def _policy_to_dict(p) -> Dict[str, Any]:
    if not p:
        return {}
    return {
        "score": getattr(p, "score", 0),
        "direction": getattr(p, "direction", "中性"),
        "summary": getattr(p, "summary", ""),
        "hits": list(getattr(p, "hits", []) or []),
        "details": getattr(p, "details", {}) or {},
    }


def _df_to_records(df) -> List[Dict[str, Any]]:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    return df.to_dict(orient="records")


# ---------------------------------------------------------------------------
# 季报新鲜度
# ---------------------------------------------------------------------------

_QUARTER_END = {"1": (3, 31), "2": (6, 30), "3": (9, 30), "4": (12, 31)}


def _parse_quarter_str(q: str) -> Optional[datetime]:
    """解析 '2025年3季度' / '2025-09-30' / '2025Q3' 等格式, 返回季度末日期"""
    if not q:
        return None
    s = str(q).strip()
    # 2025-09-30
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    # 2025年3季度 / 2025年3Q / 2025Q3
    m = re.match(r"^(\d{4}).*?([1-4]).*?季", s)
    if m:
        y = int(m.group(1))
        month, day = _QUARTER_END[m.group(2)]
        return datetime(y, month, day)
    m = re.match(r"^(\d{4})\s*Q([1-4])", s, re.I)
    if m:
        y = int(m.group(1))
        month, day = _QUARTER_END[m.group(2)]
        return datetime(y, month, day)
    return None


def _label_age(age_days: int) -> str:
    if age_days <= 60:
        return "fresh"
    if age_days <= 110:
        return "ok"
    if age_days <= 150:
        return "stale"
    return "very_stale"


# ---------------------------------------------------------------------------
# 财经新闻过滤 (剔除娱乐/体育/明星/综艺)
# ---------------------------------------------------------------------------

_FINANCE_KEYWORDS = (
    # 市场
    "股", "A股", "港股", "美股", "创业板", "科创板", "北证", "上证", "深证",
    "恒生", "纳指", "道指", "标普", "纳斯达克", "指数",
    # 标的
    "基金", "ETF", "债券", "国债", "可转债", "期货", "外汇",
    # 货币 / 利率
    "美元", "人民币", "欧元", "日元", "汇率", "降息", "加息", "利率", "LPR", "MLF",
    "美联储", "鲍威尔",
    # 监管 / 政策
    "央行", "证监会", "银保监", "财政部", "国务院", "发改委", "统计局", "工信部",
    "政策", "监管", "调控", "刺激", "稳增长", "新政",
    # 宏观
    "GDP", "CPI", "PPI", "PMI", "通胀", "通缩", "就业", "失业", "消费指数",
    "贸易", "进出口", "关税", "顺差", "逆差",
    # 行业
    "半导体", "芯片", "新能源", "光伏", "锂电", "储能", "医药", "生物医药",
    "白酒", "消费", "券商", "银行", "保险", "地产", "房地产", "物业",
    "金融", "互联网", "AI", "人工智能", "算力", "数据中心", "军工",
    "新能源车", "新能车", "汽车", "机器人", "航空", "航天",
    # 公司动作
    "业绩", "净利", "营收", "财报", "IPO", "上市", "并购", "重组", "回购",
    "增持", "减持", "分红", "配股", "定增", "停牌", "复牌",
    # 大宗
    "原油", "石油", "黄金", "白银", "铜", "铁矿", "锂", "钴",
    # 北向 / 机构
    "北向", "南向", "外资", "机构", "主力", "游资", "险资", "社保",
)

_NON_FINANCE_BLOCKLIST = (
    "娱乐圈", "明星", "艺人", "综艺", "偶像", "选秀", "粉丝",
    "世界杯", "欧冠", "奥运", "奥运会", "世锦赛", "冠军赛",
    "红毯", "绯闻", "出轨", "离婚", "分手",
)


def _filter_finance_news(items: list) -> list:
    if not items:
        return items
    out = []
    for it in items:
        text = (it.get("title") or "") + " " + (it.get("summary") or "")
        if any(b in text for b in _NON_FINANCE_BLOCKLIST):
            continue
        if any(k in text for k in _FINANCE_KEYWORDS):
            out.append(it)
    return out


def compute_freshness(holdings_df, industries_df) -> Dict[str, Any]:
    """优先用 industries.report_date (精确日期), 回落到 top_holdings.quarter"""
    dt: Optional[datetime] = None
    source = None
    as_of = None

    if isinstance(industries_df, pd.DataFrame) and not industries_df.empty and "report_date" in industries_df.columns:
        rd = industries_df["report_date"].iloc[0]
        try:
            if isinstance(rd, pd.Timestamp):
                dt = rd.to_pydatetime()
            else:
                dt = datetime.strptime(str(rd)[:10], "%Y-%m-%d")
            source = "report_date"
            as_of = dt.strftime("%Y-%m-%d")
        except Exception:
            dt = None

    if dt is None and isinstance(holdings_df, pd.DataFrame) and not holdings_df.empty and "quarter" in holdings_df.columns:
        q = holdings_df["quarter"].iloc[0]
        dt = _parse_quarter_str(str(q))
        if dt is not None:
            source = "quarter"
            as_of = str(q)

    if dt is None:
        return {"age_days": None, "label": "unknown", "source": None, "as_of": None}

    age = (datetime.now() - dt).days
    return {"age_days": age, "label": _label_age(age), "source": source, "as_of": as_of}


# ---------------------------------------------------------------------------
# 桶实现
# ---------------------------------------------------------------------------

class Scheduler:
    """把 cli.py 里的采集函数搬成后台循环"""

    def __init__(self, cfg: Config, store: StateStore) -> None:
        self.cfg = cfg
        self.store = store
        self._threads: List[threading.Thread] = []
        self._stop = threading.Event()
        # 共享状态: sector_data 由 sector 桶更新, 被 funds 桶读取做主题映射
        self._shared_lock = threading.Lock()
        self._sector_data: List[Dict[str, Any]] = []
        self._valuations: Dict[str, Dict] = {}
        self._breadth: Dict[str, Any] = {}
        self._margin_history = pd.DataFrame()
        self._north_total: float = 0.0
        self._market_tech = None
        self._sentiment = None
        self._policy = None
        self._attribution: Dict[str, List[Dict]] = {}
        self._freshness_map: Dict[str, Dict[str, Any]] = {}

    # -------------------------------------------------------- public API

    def start(self) -> None:
        targets = [
            ("funds", INTERVAL_FUNDS, self._tick_funds),
            ("market", INTERVAL_MARKET, self._tick_market),
            ("sector", INTERVAL_SECTOR, self._tick_sector),
            ("news", INTERVAL_NEWS, self._tick_news),
            ("valuations", INTERVAL_VALUATIONS, self._tick_valuations),
            ("holdings", INTERVAL_HOLDINGS, self._tick_holdings),
        ]
        for name, interval, fn in targets:
            t = threading.Thread(
                target=self._loop,
                args=(name, interval, fn),
                name=f"scheduler-{name}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
        log.info(f"scheduler 启动, 共 {len(self._threads)} 个后台桶")

    def stop(self) -> None:
        self._stop.set()

    # -------------------------------------------------------- loop

    def _loop(self, name: str, interval: int, fn) -> None:
        time.sleep(WARMUP_OFFSET.get(name, 0.0))
        while not self._stop.is_set():
            start = time.time()
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                log.warning(f"[{name}] tick 失败: {e}")
                try:
                    self.store.push_error(name, str(e))
                except Exception:
                    pass
            elapsed = time.time() - start
            wait = max(1.0, interval - elapsed)
            # 可中断的 sleep, 让 stop 快速退出
            self._stop.wait(wait)

    # -------------------------------------------------------- market

    def _tick_market(self) -> None:
        health = dict(self.store.get("data_health") or {})
        (
            market_data,
            market_tech,
            north_money,
            sector_data,
            index_histories,
            north_history,
            margin_history,
            breadth,
            overseas,
            valuations,
        ) = _collect_market(self.cfg, health)

        with self._shared_lock:
            self._sector_data = sector_data or []
            self._north_total = north_money or 0.0
            self._market_tech = market_tech
            self._breadth = breadth or {}
            self._margin_history = margin_history if margin_history is not None else pd.DataFrame()
            self._valuations = valuations or {}

        market_payload = {
            "indices": market_data or {},
            "north_money_total": north_money or 0.0,
            "breadth": breadth or {},
            "overseas": overseas or {},
            "sectors": sector_data or [],
            "index_histories": {k: _df_to_records(v) for k, v in (index_histories or {}).items()},
            "north_history": _df_to_records(north_history),
            "margin_history": _df_to_records(margin_history),
            "updated_at": time.time(),
        }
        self.store.update({
            "market": market_payload,
            "market_tech": _tech_to_dict(market_tech),
            "valuations": valuations or {},
            "data_health": health,
        })

        # SQLite 持久化 (异常不影响桶)
        try:
            db.upsert_index_history(index_histories or {})
            db.upsert_north_history(north_history)
        except Exception as e:  # noqa: BLE001
            log.debug(f"db 大盘落盘失败: {e}")

    # -------------------------------------------------------- sector

    def _tick_sector(self) -> None:
        # sector 其实已经在 market 桶里一并拉了; 这里单独跑是为了让它有更高频的刷新
        # (cli._collect_market 调用成本较高, sector 里只动板块流向)
        from fund_advisor.data.cache import cached
        from fund_advisor.data.market import get_sector_flow

        health = dict(self.store.get("data_health") or {})
        try:
            df = cached(key="sector_flow_15", ttl=INTERVAL_SECTOR, fetch=lambda: get_sector_flow(top_n=15))
            sectors: List[Dict[str, Any]] = []
            if isinstance(df, pd.DataFrame) and not df.empty:
                for _, row in df.iterrows():
                    sectors.append({
                        "sector": row.get("sector"),
                        "change_pct": row.get("change_pct"),
                        "main_net_flow": row.get("main_net_flow"),
                    })
            health["sector_flow"] = "ok" if sectors else "empty"
            with self._shared_lock:
                self._sector_data = sectors
            market = dict(self.store.get("market") or {})
            market["sectors"] = sectors
            self.store.update({"market": market, "data_health": health})
        except Exception as e:  # noqa: BLE001
            health["sector_flow"] = "failed"
            self.store.update({"data_health": health}, broadcast=False)
            raise e

    # -------------------------------------------------------- news

    def _tick_news(self) -> None:
        health = dict(self.store.get("data_health") or {})
        news, news_recent, sentiment, policy, policy_news_recent = _collect_news(health)
        with self._shared_lock:
            self._sentiment = sentiment
            self._policy = policy

        raw = news_recent or news or []
        filtered = _filter_finance_news(raw)
        if not filtered and raw:
            # 关键词全灭火时退回原始列表, 避免前端完全空白
            filtered = raw

        policy_items = list(policy_news_recent or [])

        try:
            db.upsert_news(filtered, "finance")
            db.upsert_news(policy_items, "policy")
        except Exception as e:  # noqa: BLE001
            log.debug(f"db 新闻落盘失败: {e}")

        self.store.update({
            "news": filtered[:80],
            "policy_news": policy_items[:30],
            "sentiment": _sentiment_to_dict(sentiment),
            "policy": _policy_to_dict(policy),
            "data_health": health,
        })

    # -------------------------------------------------------- valuations

    def _tick_valuations(self) -> None:
        # 这些指标日级更新, 只是兜底刷新; market 桶里也会顺便刷
        from fund_advisor.data.cache import cached
        from fund_advisor.data.valuation import get_valuations
        from fund_advisor.data.market import get_margin_balance, get_overseas_indices, get_market_breadth

        health = dict(self.store.get("data_health") or {})
        try:
            valuations = cached(key="valuations", ttl=INTERVAL_VALUATIONS, fetch=get_valuations) or {}
            margin_history = cached(
                key="margin_balance_20", ttl=4 * 3600, fetch=lambda: get_margin_balance(days=20)
            )
            overseas = cached(key="overseas_indices", ttl=4 * 3600, fetch=get_overseas_indices) or {}
            breadth = cached(key="market_breadth", ttl=600, fetch=get_market_breadth) or {}
            health["valuations"] = "ok" if valuations else "empty"
            health["margin_history"] = "ok" if (margin_history is not None and not margin_history.empty) else "empty"
            with self._shared_lock:
                self._valuations = valuations
                self._margin_history = margin_history if margin_history is not None else pd.DataFrame()
                self._breadth = breadth
            market = dict(self.store.get("market") or {})
            market["overseas"] = overseas
            market["breadth"] = breadth
            market["margin_history"] = _df_to_records(margin_history)
            self.store.update({
                "market": market,
                "valuations": valuations,
                "data_health": health,
            })

            try:
                today = datetime.now().strftime("%Y-%m-%d")
                db.upsert_valuations(today, valuations or {})
                if margin_history is not None and not margin_history.empty:
                    db.upsert_margin_history(margin_history)
                if breadth:
                    db.upsert_breadth_daily(today, breadth)
                db.cleanup(365)
                db.vacuum_if_due()
            except Exception as e:  # noqa: BLE001
                log.debug(f"db valuations 落盘失败: {e}")
        except Exception as e:  # noqa: BLE001
            health["valuations"] = "failed"
            self.store.update({"data_health": health}, broadcast=False)
            raise e

    # -------------------------------------------------------- holdings

    def _tick_holdings(self) -> None:
        health = dict(self.store.get("data_health") or {})
        holdings = _collect_fund_holdings(self.cfg.funds)
        etf_premium = _collect_etf_premium(self.cfg.funds, health)

        serializable: Dict[str, Any] = {}
        attribution: Dict[str, List[Dict]] = {}
        freshness_map: Dict[str, Dict[str, Any]] = {}
        for code, blob in (holdings or {}).items():
            top_df = blob.get("top_holdings")
            ind_df = blob.get("industries")
            serializable[code] = {
                "top_holdings": _df_to_records(top_df),
                "industries": _df_to_records(ind_df),
                "attribution": list(blob.get("attribution") or []),
                "freshness": compute_freshness(top_df, ind_df),
            }
            attribution[code] = list(blob.get("attribution") or [])
            freshness_map[code] = serializable[code]["freshness"]

        with self._shared_lock:
            self._attribution = attribution
            self._freshness_map = freshness_map

        self.store.update({
            "fund_holdings": serializable,
            "etf_premium": etf_premium or {},
            "data_health": health,
        })

    # -------------------------------------------------------- funds

    def _tick_funds(self) -> None:
        with self._shared_lock:
            sector_data = list(self._sector_data)
            valuations = dict(self._valuations)
            breadth = dict(self._breadth)
            margin_history = self._margin_history
            north_total = self._north_total
            market_tech = self._market_tech
            sentiment = self._sentiment
            policy = self._policy
            attribution_map = dict(self._attribution)
            freshness_map = dict(self._freshness_map)

        summaries: List[Dict[str, Any]] = []
        decisions: List[Dict[str, Any]] = []
        histories: Dict[str, List[Dict[str, Any]]] = {}

        for fund in self.cfg.funds:
            try:
                summary, fund_tech, hist_30d = _summarize_fund(fund)
            except Exception as e:  # noqa: BLE001
                self.store.push_error(f"fund_{fund.code}", str(e))
                continue

            # 盘中偏差追踪: 记录当前估算, 并用历史净值核对过去的估算
            try:
                est_pct = summary.get("estimate_pct")
                est_time = summary.get("estimate_time")
                if isinstance(est_pct, (int, float)) and est_time:
                    estimate_bias.record_estimate(fund.code, est_pct, est_time)
                # 用全量 history (不只是 30 天) 做核对会更好, 但 _summarize_fund 里已经截到 30 天够用
                estimate_bias.reconcile_with_history(fund.code, hist_30d)
                summary["bias"] = estimate_bias.get_stats(fund.code)
            except Exception as e:  # noqa: BLE001
                log.debug(f"bias {fund.code} 失败: {e}")

            # 季报新鲜度 (由 _tick_holdings 周度刷新)
            fresh = freshness_map.get(fund.code)
            if fresh:
                summary["holdings_freshness"] = fresh

            summaries.append(summary)
            histories[fund.code] = _df_to_records(hist_30d)

            sector_flow = _sector_flow_for_theme(sector_data, summary.get("theme", ""), self.cfg)
            attribution = attribution_map.get(fund.code) or []
            try:
                decision = make_decision(
                    fund_code=fund.code,
                    fund_name=summary["name"],
                    fund_tech=fund_tech,
                    market_tech=market_tech,
                    sentiment=sentiment,
                    policy=policy,
                    north_money=north_total,
                    sector_net_flow=sector_flow,
                    valuations=valuations,
                    breadth=breadth,
                    margin_history=margin_history,
                    attribution=attribution,
                )
                decisions.append(_decision_to_dict(decision))
            except Exception as e:  # noqa: BLE001
                self.store.push_error(f"decision_{fund.code}", str(e))

        self.store.update({
            "funds": summaries,
            "fund_decisions": decisions,
            "fund_histories": histories,
        })
        # 每轮 funds tick 末尾落盘一次 bias 数据
        try:
            estimate_bias.flush()
        except Exception as e:  # noqa: BLE001
            log.debug(f"estimate_bias flush 失败: {e}")


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_SCHEDULER: Scheduler | None = None


def start_scheduler(config_path: str = "config/holdings.yaml") -> Scheduler:
    """创建 (若未创建) 并启动 scheduler"""
    global _SCHEDULER
    if _SCHEDULER is not None:
        return _SCHEDULER
    cfg = load_config(config_path)
    store = get_store()
    _SCHEDULER = Scheduler(cfg, store)
    _SCHEDULER.start()
    return _SCHEDULER


def get_scheduler() -> Scheduler | None:
    return _SCHEDULER
