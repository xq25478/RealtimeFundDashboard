"""命令行编排：把数据获取、分析、报告串成一条流水线"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
from rich.console import Console

from fund_advisor.utils.config import load_config, Config
from fund_advisor.utils.logger import get_logger
from fund_advisor.data.cache import cached
from fund_advisor.data.fund import get_fund_realtime, get_fund_history
from fund_advisor.data.market import (
    get_index_realtime,
    get_index_data,
    get_north_money,
    get_north_money_history,
    get_sector_flow,
    get_margin_balance,
    get_market_breadth,
    get_overseas_indices,
    INDEX_NAME_MAP,
)
from fund_advisor.data.news import (
    get_finance_news,
    get_policy_news,
    get_recent_news,
    get_recent_policy_news,
)
from fund_advisor.data.valuation import get_valuations
from fund_advisor.data.fund_holding import (
    get_fund_top_holdings,
    get_fund_industry_allocation,
    attribute_fund_today,
)
from fund_advisor.data.etf_premium import get_etf_premium, _is_etf_code
from fund_advisor.analysis.technical import analyze_technical, TechnicalSignal
from fund_advisor.analysis.sentiment import analyze_sentiment
from fund_advisor.analysis.policy import analyze_policy
from fund_advisor.analysis.decision import make_decision
from fund_advisor.report.console import render_console_report
from fund_advisor.report.html_report import render_html_report
from fund_advisor.utils.history import append_decisions, backtest as run_backtest_logic


log = get_logger("fund_advisor.cli")
console = Console()


# ============================================================================
# TTL 配置（秒）—— 数据更新频率决定缓存窗口
# ============================================================================
TTL_REALTIME = 300        # 5 min: 盘中行情/估值/成交
TTL_INTRADAY = 600        # 10 min: 北向、板块流向、情绪
TTL_DAILY = 4 * 3600      # 4 hr: 收盘 K 线 / 两融 / 海外
TTL_VALUATION = 12 * 3600 # 12 hr: PE/PB 分位
TTL_NEWS = 1800           # 30 min: 政策/新闻
TTL_QUARTER = 7 * 86400   # 7 d: 季报口径持仓


def _safe(stage: str, fetch_fn, default, health: Dict[str, str]):
    """统一的"取数据 + 记录健康状态"包装。"""
    try:
        result = fetch_fn()
    except Exception as e:
        log.debug(f"{stage} 失败: {e}")
        health[stage] = "failed"
        return default

    is_empty = (
        result is None
        or (isinstance(result, pd.DataFrame) and result.empty)
        or (isinstance(result, (dict, list)) and len(result) == 0)
    )
    health[stage] = "empty" if is_empty else "ok"
    return result


def _collect_market(cfg: Config, health: Dict[str, str]):
    """采集大盘行情、北向资金、板块资金、大盘技术指标、近30日指数日线"""
    log.info("正在采集大盘数据...")

    core_indices = cfg.watchlist.indices if cfg and cfg.watchlist.indices else list(INDEX_NAME_MAP.keys())
    core_names = {INDEX_NAME_MAP.get(s) for s in core_indices}
    core_names.discard(None)

    realtime = _safe(
        "market_realtime",
        lambda: cached(key="index_realtime", ttl=TTL_REALTIME, fetch=get_index_realtime),
        pd.DataFrame(),
        health,
    )

    market_data: Dict[str, Dict] = {}
    if isinstance(realtime, pd.DataFrame) and not realtime.empty:
        for _, row in realtime.iterrows():
            name = row.get("name") or row.get("名称")
            if not name:
                continue
            if core_names and str(name) not in core_names:
                continue
            market_data[str(name)] = {
                "price": row.get("price"),
                "change_pct": row.get("change_pct"),
                "amount": row.get("amount"),
            }

    if not market_data:
        for sym in core_indices:
            try:
                hist = cached(
                    key=f"index_data_{sym}",
                    ttl=TTL_DAILY,
                    fetch=lambda s=sym: get_index_data(s),
                )
                if hist is not None and not hist.empty:
                    last = hist.iloc[-1]
                    market_data[INDEX_NAME_MAP.get(sym, sym)] = {
                        "price": float(last.get("close", 0)),
                        "change_pct": float(last.get("change_pct", 0)),
                        "amount": float(last.get("amount", 0)) * 1e8 if "amount" in last else None,
                    }
            except Exception:
                continue

    # 主要指数 30 日日线（用于 LLM 上下文）
    sh_hist = cached(key="index_data_sh000001", ttl=TTL_DAILY, fetch=lambda: get_index_data("sh000001"))
    sz_hist = cached(key="index_data_sz399001", ttl=TTL_DAILY, fetch=lambda: get_index_data("sz399001"))
    cyb_hist = cached(key="index_data_sz399006", ttl=TTL_DAILY, fetch=lambda: get_index_data("sz399006"))
    market_tech = analyze_technical(sh_hist, price_col="close")

    def _tail30(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        return df.tail(30).reset_index(drop=True)

    index_histories = {
        "上证指数": _tail30(sh_hist),
        "深证成指": _tail30(sz_hist),
        "创业板指": _tail30(cyb_hist),
    }

    # 北向资金当日
    north = _safe(
        "north_money",
        lambda: cached(key="north_money", ttl=TTL_INTRADAY, fetch=get_north_money),
        {},
        health,
    )
    north_total = 0.0
    if north:
        for k, v in north.items():
            if any(s in str(k) for s in ("北向", "合计", "总计", "净")):
                try:
                    north_total = float(v)
                    break
                except (TypeError, ValueError):
                    continue
        if not north_total:
            try:
                north_total = float(sum(v for v in north.values() if isinstance(v, (int, float))))
            except Exception:
                north_total = 0.0

    north_history = _safe(
        "north_history",
        lambda: cached(key="north_history_10", ttl=TTL_INTRADAY, fetch=lambda: get_north_money_history(days=10)),
        pd.DataFrame(),
        health,
    )

    sector_df = _safe(
        "sector_flow",
        lambda: cached(key="sector_flow_15", ttl=TTL_INTRADAY, fetch=lambda: get_sector_flow(top_n=15)),
        pd.DataFrame(),
        health,
    )
    sector_data: List[Dict] = []
    if isinstance(sector_df, pd.DataFrame) and not sector_df.empty:
        for _, row in sector_df.iterrows():
            sector_data.append({
                "sector": row.get("sector"),
                "change_pct": row.get("change_pct"),
                "main_net_flow": row.get("main_net_flow"),
            })

    margin_history = _safe(
        "margin_history",
        lambda: cached(key="margin_balance_20", ttl=TTL_DAILY, fetch=lambda: get_margin_balance(days=20)),
        pd.DataFrame(),
        health,
    )

    breadth = _safe(
        "breadth",
        lambda: cached(key="market_breadth", ttl=TTL_INTRADAY, fetch=get_market_breadth),
        {},
        health,
    )

    overseas = _safe(
        "overseas",
        lambda: cached(key="overseas_indices", ttl=TTL_DAILY, fetch=get_overseas_indices),
        {},
        health,
    )

    valuations = _safe(
        "valuations",
        lambda: cached(key="valuations", ttl=TTL_VALUATION, fetch=get_valuations),
        {},
        health,
    )

    return (
        market_data, market_tech, north_total, sector_data,
        index_histories, north_history,
        margin_history, breadth, overseas, valuations,
    )


def _collect_news(health: Dict[str, str]):
    """采集新闻 + 政策面: 当日热条 + 近 7 日时间窗"""
    log.info("正在采集财经新闻 & 政策面...")
    news = _safe(
        "news_today",
        lambda: cached(key="finance_news_200", ttl=TTL_NEWS, fetch=lambda: get_finance_news(limit=200)),
        [],
        health,
    )
    news_recent = _safe(
        "news_recent_7d",
        lambda: cached(key="news_recent_7d", ttl=TTL_NEWS, fetch=lambda: get_recent_news(days=7, fetch=400)),
        [],
        health,
    )
    policy_news_recent = _safe(
        "policy_news_recent_7d",
        lambda: cached(key="policy_news_recent_7d", ttl=TTL_NEWS, fetch=lambda: get_recent_policy_news(days=7, fetch=500)),
        [],
        health,
    )

    sentiment = analyze_sentiment(news_recent or news)
    policy_corpus = (policy_news_recent or get_policy_news(limit=20)) + (news_recent or news)[:30]
    policy = analyze_policy(policy_corpus)

    return news, news_recent, sentiment, policy, policy_news_recent


def _summarize_fund(fund) -> Tuple[Dict, TechnicalSignal, pd.DataFrame]:
    """单只基金的实时估值 + 历史技术分析, 同时返回近 30 日净值序列"""
    realtime = cached(
        key=f"fund_realtime_{fund.code}",
        ttl=60,
        fetch=lambda: get_fund_realtime(fund.code),
    )
    if not realtime.get("name"):
        realtime["name"] = fund.name

    history = cached(
        key=f"fund_history_{fund.code}_180",
        ttl=TTL_DAILY,
        fetch=lambda: get_fund_history(fund.code, days=180),
    )
    tech = analyze_technical(history, price_col="nav")

    summary = {
        "code": fund.code,
        "name": realtime.get("name") or fund.name,
        "theme": getattr(fund, "theme", "") or "",
        "last_nav": realtime.get("last_nav"),
        "estimate_nav": realtime.get("estimate_nav"),
        "estimate_pct": realtime.get("estimate_pct"),
        "estimate_time": realtime.get("estimate_time"),
        "volatility": tech.volatility,
        "max_drawdown": tech.max_drawdown,
        "change_20d": tech.details.get("change_20d"),
    }

    history_30d = history.tail(30).reset_index(drop=True) if (history is not None and not history.empty) else pd.DataFrame()
    return summary, tech, history_30d


def _collect_fund_holdings(funds, top_n: int = 10) -> Dict[str, Dict]:
    """采集每只基金的穿透持仓 + 行业配置 + 重仓股当日涨跌归因。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out: Dict[str, Dict] = {}

    if not funds:
        return out

    log.info(f"正在采集 {len(funds)} 只基金的穿透持仓...")

    holdings_map: Dict[str, pd.DataFrame] = {}
    industries_map: Dict[str, pd.DataFrame] = {}
    for fund in funds:
        try:
            holdings_map[fund.code] = cached(
                key=f"holdings_{fund.code}_{top_n}",
                ttl=TTL_QUARTER,
                fetch=lambda f=fund: get_fund_top_holdings(f.code, top_n=top_n),
            )
        except Exception as e:
            log.debug(f"持仓 {fund.code} 失败: {e}")
            holdings_map[fund.code] = pd.DataFrame()
        try:
            industries_map[fund.code] = cached(
                key=f"industries_{fund.code}",
                ttl=TTL_QUARTER,
                fetch=lambda f=fund: get_fund_industry_allocation(f.code, top_n=6),
            )
        except Exception as e:
            log.debug(f"行业 {fund.code} 失败: {e}")
            industries_map[fund.code] = pd.DataFrame()

    # 重仓股归因 (当日变化, 不缓存太久)
    def _attr(code: str, df: pd.DataFrame) -> Tuple[str, list]:
        try:
            return code, cached(
                key=f"attribution_{code}",
                ttl=TTL_REALTIME,
                fetch=lambda: attribute_fund_today(df),
            )
        except Exception as e:
            log.debug(f"归因 {code} 失败: {e}")
            return code, []

    attribution_map: Dict[str, list] = {c: [] for c in holdings_map}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(funds)))) as ex:
        futs = {ex.submit(_attr, c, h): c for c, h in holdings_map.items() if not h.empty}
        for fut in as_completed(futs):
            code, rows = fut.result()
            attribution_map[code] = rows

    for fund in funds:
        out[fund.code] = {
            "top_holdings": holdings_map.get(fund.code, pd.DataFrame()),
            "industries": industries_map.get(fund.code, pd.DataFrame()),
            "attribution": attribution_map.get(fund.code, []),
        }
    return out


def _collect_etf_premium(funds, health: Dict[str, str]) -> Dict[str, Dict]:
    """对组合中场内 ETF 拉折溢价"""
    etf_codes = [f.code for f in funds if _is_etf_code(f.code)]
    if not etf_codes:
        health["etf_premium"] = "empty"
        return {}
    return _safe(
        "etf_premium",
        lambda: cached(
            key=f"etf_premium_{'_'.join(sorted(etf_codes))}",
            ttl=TTL_REALTIME,
            fetch=lambda: get_etf_premium(etf_codes),
        ),
        {},
        health,
    )


def _sector_flow_for_theme(sector_data: List[Dict], theme: str, cfg: Config) -> float:
    """从板块流向数据中找出与基金主题最相关的板块净流入(亿元).
    主题→关键词映射来自 config (yaml + 默认表)."""
    keys = cfg.keywords_for_theme(theme) if cfg else []
    if not keys:
        keys = [theme] if theme else []
    if not keys:
        return 0.0
    for row in sector_data:
        sec = str(row.get("sector", ""))
        if any(k in sec for k in keys):
            net = row.get("main_net_flow") or 0
            return float(net) / 1e8 if isinstance(net, (int, float)) else 0
    return 0.0


def run_analyze(config_path: str, html: bool = False, llm: bool = False):
    """完整分析流程"""
    cfg = load_config(config_path)

    if not cfg.funds:
        console.print("[yellow]配置文件中尚未填写任何基金，请先在 holdings.yaml 中添加。[/]")
        return

    health: Dict[str, str] = {}

    (market_data, market_tech, north_money, sector_data,
     index_histories, north_history,
     margin_history, breadth, overseas, valuations) = _collect_market(cfg, health)
    news, news_recent, sentiment, policy, policy_news_recent = _collect_news(health)

    # 基金穿透持仓 + 当日重仓股归因
    try:
        fund_holdings_data = _collect_fund_holdings(cfg.funds)
        ok = sum(1 for v in fund_holdings_data.values() if (v.get("top_holdings") is not None and not v["top_holdings"].empty))
        health["fund_holdings"] = "ok" if ok else "empty"
    except Exception as e:
        log.debug(f"基金穿透采集整体失败: {e}")
        fund_holdings_data = {}
        health["fund_holdings"] = "failed"

    # 场内 ETF 折溢价
    etf_premium = _collect_etf_premium(cfg.funds, health)

    fund_summaries: List[Dict] = []
    fund_histories: Dict[str, pd.DataFrame] = {}
    decisions = []

    log.info(f"正在分析 {len(cfg.funds)} 只基金...")
    for fund in cfg.funds:
        summary, fund_tech, fund_hist_30d = _summarize_fund(fund)
        fund_summaries.append(summary)
        fund_histories[fund.code] = fund_hist_30d

        sector_flow = _sector_flow_for_theme(sector_data, summary.get("theme", ""), cfg)
        attribution = (fund_holdings_data.get(fund.code) or {}).get("attribution") or []

        decision = make_decision(
            fund_code=fund.code,
            fund_name=summary["name"],
            fund_tech=fund_tech,
            market_tech=market_tech,
            sentiment=sentiment,
            policy=policy,
            north_money=north_money,
            sector_net_flow=sector_flow,
            valuations=valuations,
            breadth=breadth,
            margin_history=margin_history,
            attribution=attribution,
        )
        decisions.append(decision)

    # 控制台 / HTML 报告仍使用当日热条新闻 (简洁), LLM 喂给 7 日全量
    news_for_console = news_recent[:10] if news_recent else news[:10]

    render_console_report(
        market_data=market_data,
        sector_data=sector_data,
        fund_summaries=fund_summaries,
        decisions=decisions,
        sentiment=sentiment,
        policy=policy,
        north_money=north_money,
        market_tech=market_tech,
        news_top=news_for_console,
        valuations=valuations,
        margin_history=margin_history,
        breadth=breadth,
        overseas=overseas,
        etf_premium=etf_premium,
        data_health=health,
    )

    # 持久化今日决策
    try:
        append_decisions(decisions, fund_summaries)
    except Exception as e:
        log.debug(f"决策落盘失败: {e}")

    # 1) LLM 投资指南
    advice_text: str = ""
    advice_model: str = ""
    if llm:
        try:
            from fund_advisor.advisor import generate_advice
            from fund_advisor.advisor.llm_client import get_model
            advice_model = get_model()
            console.print(f"[cyan]→ 调用 Claude [{advice_model}] 生成投资指南...[/]")
            advice_text = generate_advice(
                market_data=market_data,
                sector_data=sector_data,
                fund_summaries=fund_summaries,
                decisions=decisions,
                sentiment=sentiment,
                policy=policy,
                north_money=north_money,
                market_tech=market_tech,
                news_top=(news_recent or news)[:60],
                index_histories=index_histories,
                fund_histories=fund_histories,
                north_history=north_history,
                policy_news_recent=policy_news_recent,
                margin_history=margin_history,
                breadth=breadth,
                overseas=overseas,
                valuations=valuations,
                fund_holdings=fund_holdings_data,
                etf_premium=etf_premium,
                data_health=health,
            )
            console.print(f"[green]✓[/] Claude 投资指南生成完成 (~{len(advice_text):,} 字)")
        except Exception as e:
            log.error(f"调用 Claude API 失败：{e}")
            console.print(f"[red]✗ Claude 投资指南生成失败：{e}[/]")

    # 2) HTML 报告
    want_html = html or llm or "html" in cfg.settings.report_format
    if want_html:
        out = render_html_report(
            output_dir="reports",
            market_data=market_data,
            sector_data=sector_data,
            fund_summaries=fund_summaries,
            decisions=decisions,
            sentiment=sentiment,
            policy=policy,
            north_money=north_money,
            market_tech=market_tech,
            news_top=news_for_console,
            llm_advice=advice_text or None,
            llm_model=advice_model or None,
            valuations=valuations,
            margin_history=margin_history,
            breadth=breadth,
            overseas=overseas,
            etf_premium=etf_premium,
            fund_holdings=fund_holdings_data,
            data_health=health,
        )
        console.print(f"[green]✓[/] HTML 报告已生成：[bold]{out}[/]")
        try:
            import os, sys
            if os.environ.get("FUND_NO_AUTO_OPEN") != "1":
                if sys.platform == "darwin":
                    os.system(f"open '{out}'")
                elif sys.platform.startswith("linux"):
                    os.system(f"xdg-open '{out}' >/dev/null 2>&1 &")
        except Exception:
            pass


def run_holdings(config_path: str):
    cfg = load_config(config_path)
    if not cfg.funds:
        console.print("[yellow]尚未配置任何基金。[/]")
        return

    from rich.table import Table
    table = Table(title="我的基金", show_lines=False)
    table.add_column("代码")
    table.add_column("名称")
    table.add_column("题材")
    table.add_column("最新净值", justify="right")
    table.add_column("盘中估算涨跌", justify="right")

    for fund in cfg.funds:
        rt = cached(
            key=f"fund_realtime_{fund.code}",
            ttl=60,
            fetch=lambda f=fund: get_fund_realtime(f.code),
        )
        pct = rt.get("estimate_pct")
        pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "-"
        style = "red" if isinstance(pct, (int, float)) and pct > 0 else "green" if isinstance(pct, (int, float)) and pct < 0 else "white"
        table.add_row(
            fund.code,
            rt.get("name") or fund.name,
            getattr(fund, "theme", ""),
            f"{rt.get('last_nav'):.4f}" if isinstance(rt.get('last_nav'), (int, float)) else "-",
            f"[{style}]{pct_str}[/]",
        )
    console.print(table)


def run_market():
    health: Dict[str, str] = {}
    (market_data, market_tech, north_money, sector_data,
     _, _, margin_history, breadth, overseas, valuations) = _collect_market(
        load_config('config/holdings.yaml') if Path('config/holdings.yaml').exists() else None,
        health,
    )
    render_console_report(
        market_data=market_data,
        sector_data=sector_data,
        fund_summaries=[],
        decisions=[],
        sentiment=analyze_sentiment([]),
        policy=analyze_policy([]),
        north_money=north_money,
        market_tech=market_tech,
        news_top=[],
        valuations=valuations,
        margin_history=margin_history,
        breadth=breadth,
        overseas=overseas,
        etf_premium={},
        data_health=health,
    )


def run_news(limit: int = 10):
    news = get_finance_news(limit=limit)
    if not news:
        console.print("[yellow]暂无新闻数据[/]")
        return
    console.print(f"\n[bold cyan]财经新闻 TOP {limit}[/]\n")
    for i, item in enumerate(news, 1):
        console.print(f"[bold]{i:>2}.[/] {item.get('title','')}")
        if item.get("time"):
            console.print(f"     [dim]{item.get('time')}[/]")


def run_backtest(lookback_days: int = 90):
    """根据 history.jsonl 统计每种 action 的 5 日 / 20 日胜率"""
    summary = run_backtest_logic(lookback_days=lookback_days)
    if not summary:
        console.print("[yellow]暂无足够历史决策数据,先跑几天 analyze 再回看。[/]")
        return

    from rich.table import Table
    table = Table(
        title=f"决策回测 · 最近 {lookback_days} 天",
        show_lines=False,
    )
    table.add_column("动作", style="bold")
    table.add_column("样本", justify="right")
    table.add_column("5日胜率", justify="right")
    table.add_column("5日均值收益", justify="right")
    table.add_column("20日胜率", justify="right")
    table.add_column("20日均值收益", justify="right")

    order = ["买入", "加仓", "持有", "减仓", "卖出"]
    for action in order:
        s = summary.get(action)
        if not s:
            continue
        def _pct(v): return f"{v*100:.0f}%" if isinstance(v, (int, float)) else "-"
        def _ret(v): return f"{v:+.2f}%" if isinstance(v, (int, float)) else "-"
        table.add_row(
            action,
            str(s["n"]),
            _pct(s.get("win_rate_5d")),
            _ret(s.get("avg_return_5d")),
            _pct(s.get("win_rate_20d")),
            _ret(s.get("avg_return_20d")),
        )
    console.print(table)
