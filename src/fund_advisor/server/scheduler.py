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

import threading
import time
from typing import Any, Dict, List

import pandas as pd

from fund_advisor.utils.config import Config, load_config
from fund_advisor.utils.logger import get_logger
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
        "rsi": getattr(tech, "rsi", None),
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
        "details": getattr(s, "details", {}) or {},
    }


def _policy_to_dict(p) -> Dict[str, Any]:
    if not p:
        return {}
    return {
        "score": getattr(p, "score", 0),
        "summary": getattr(p, "summary", ""),
        "details": getattr(p, "details", {}) or {},
    }


def _df_to_records(df) -> List[Dict[str, Any]]:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    return df.to_dict(orient="records")


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

        self.store.update({
            "news": (news_recent or news or [])[:80],
            "policy_news": (policy_news_recent or [])[:30],
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
        for code, blob in (holdings or {}).items():
            serializable[code] = {
                "top_holdings": _df_to_records(blob.get("top_holdings")),
                "industries": _df_to_records(blob.get("industries")),
                "attribution": list(blob.get("attribution") or []),
            }
            attribution[code] = list(blob.get("attribution") or [])

        with self._shared_lock:
            self._attribution = attribution

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

        summaries: List[Dict[str, Any]] = []
        decisions: List[Dict[str, Any]] = []
        histories: Dict[str, List[Dict[str, Any]]] = {}

        for fund in self.cfg.funds:
            try:
                summary, fund_tech, hist_30d = _summarize_fund(fund)
            except Exception as e:  # noqa: BLE001
                self.store.push_error(f"fund_{fund.code}", str(e))
                continue

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
