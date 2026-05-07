"""命令行美化报告（基于 rich）"""

from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich.box import ROUNDED, SIMPLE

from fund_advisor.analysis.decision import Decision
from fund_advisor.analysis.technical import TechnicalSignal
from fund_advisor.analysis.sentiment import SentimentResult
from fund_advisor.analysis.policy import PolicySignal


ACTION_STYLE = {
    "买入": "bold white on red",
    "加仓": "bold red",
    "持有": "bold yellow",
    "减仓": "bold green",
    "卖出": "bold white on green",
}


def _color_pct(value: float) -> Text:
    """A 股配色：红涨绿跌"""
    if value > 0:
        return Text(f"+{value:.2f}%", style="bold red")
    if value < 0:
        return Text(f"{value:.2f}%", style="bold green")
    return Text("0.00%", style="dim")


def _color_amount(value: float, unit: str = "亿") -> Text:
    """金额上色（A 股配色：流入红，流出绿）"""
    if value > 0:
        return Text(f"+{value:.1f} {unit}", style="bold red")
    if value < 0:
        return Text(f"{value:.1f} {unit}", style="bold green")
    return Text(f"0 {unit}", style="dim")


def _color_score(value: float) -> Text:
    if value >= 30:
        return Text(f"{value:+.1f}", style="bold red")
    if value <= -30:
        return Text(f"{value:+.1f}", style="bold green")
    return Text(f"{value:+.1f}", style="yellow")


def _color_pct_quantile(p: float) -> Text:
    """估值分位上色：低分位绿(便宜) / 中性黄 / 高分位红(贵)"""
    if p < 30:
        return Text(f"{p:.0f}%", style="bold green")
    if p < 70:
        return Text(f"{p:.0f}%", style="yellow")
    return Text(f"{p:.0f}%", style="bold red")


def render_console_report(
    *,
    market_data: Dict,
    sector_data: List[Dict],
    fund_summaries: List[Dict],
    decisions: List[Decision],
    sentiment: SentimentResult,
    policy: PolicySignal,
    north_money: float,
    market_tech: TechnicalSignal,
    news_top: List[Dict],
    valuations: Optional[Dict[str, Dict]] = None,
    margin_history: Optional[pd.DataFrame] = None,
    breadth: Optional[Dict] = None,
    overseas: Optional[Dict[str, Dict]] = None,
    etf_premium: Optional[Dict[str, Dict]] = None,
    data_health: Optional[Dict[str, str]] = None,
):
    """渲染完整命令行报告"""
    console = Console()
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]A 股基金每日分析报告[/]  [dim]{today}[/]",
        border_style="cyan",
    ))

    _render_health(console, data_health)
    _render_market(console, market_data, market_tech, north_money)
    _render_valuations(console, valuations)
    _render_breadth(console, breadth)
    _render_margin(console, margin_history)
    _render_overseas(console, overseas)
    _render_sectors(console, sector_data)
    _render_news_summary(console, sentiment, policy, news_top)
    _render_etf_premium(console, etf_premium)
    if fund_summaries:
        _render_funds(console, fund_summaries, decisions)
    if decisions:
        _render_actions(console, decisions)
    _render_disclaimer(console)


def _render_health(console: Console, health: Optional[Dict[str, str]]):
    if not health:
        return
    failed = [k for k, v in health.items() if v == "failed"]
    empty = [k for k, v in health.items() if v == "empty"]
    if not failed and not empty:
        return
    msg_parts = []
    if failed:
        msg_parts.append(f"[red]失败: {', '.join(failed)}[/]")
    if empty:
        msg_parts.append(f"[yellow]空: {', '.join(empty)}[/]")
    console.print(Panel(
        " | ".join(msg_parts),
        title="[bold]⚠ 数据采集状态[/]",
        border_style="yellow",
    ))


def _render_market(console: Console, market_data: Dict, market_tech: TechnicalSignal, north_money: float):
    console.print(Rule("[bold]一、大盘行情[/]", style="cyan"))

    table = Table(box=ROUNDED, show_lines=False, pad_edge=False)
    table.add_column("指数", style="bold")
    table.add_column("最新点位", justify="right")
    table.add_column("涨跌幅", justify="right")
    table.add_column("成交额(亿)", justify="right")

    for name, info in market_data.items():
        price = info.get("price")
        pct = info.get("change_pct")
        amt = info.get("amount")
        table.add_row(
            name,
            f"{price:.2f}" if isinstance(price, (int, float)) else "-",
            _color_pct(pct) if isinstance(pct, (int, float)) else Text("-"),
            f"{amt/1e8:.1f}" if isinstance(amt, (int, float)) else "-",
        )

    console.print(table)

    sub = Table(box=SIMPLE, show_header=False)
    sub.add_column("项目", style="dim")
    sub.add_column("内容")
    sub.add_row("大盘趋势", f"{market_tech.trend}（{market_tech.ma_signal}）")
    sub.add_row("MACD", market_tech.macd_signal)
    sub.add_row("RSI", f"{market_tech.rsi_value:.1f}（{market_tech.rsi_signal}）")
    sub.add_row("北向资金", _color_amount(north_money) if north_money else Text("-"))
    sub.add_row("技术评分", _color_score(market_tech.score))
    console.print(sub)
    console.print()


def _render_valuations(console: Console, valuations: Optional[Dict[str, Dict]]):
    if not valuations:
        return
    console.print(Rule("[bold]一-A、指数估值分位（PE/PB · 5Y / 10Y）[/]", style="cyan"))
    table = Table(box=ROUNDED)
    table.add_column("指数", style="bold")
    table.add_column("PE", justify="right")
    table.add_column("PE 5Y", justify="right")
    table.add_column("PE 10Y", justify="right")
    table.add_column("PB", justify="right")
    table.add_column("PB 10Y", justify="right")

    for name, v in valuations.items():
        pe = v.get("pe")
        pe5 = v.get("pe_pct_5y")
        pe10 = v.get("pe_pct_10y")
        pb = v.get("pb")
        pb10 = v.get("pb_pct_10y")
        table.add_row(
            name,
            f"{pe:.2f}" if isinstance(pe, (int, float)) else "-",
            _color_pct_quantile(pe5) if isinstance(pe5, (int, float)) else Text("-"),
            _color_pct_quantile(pe10) if isinstance(pe10, (int, float)) else Text("-"),
            f"{pb:.2f}" if isinstance(pb, (int, float)) else "-",
            _color_pct_quantile(pb10) if isinstance(pb10, (int, float)) else Text("-"),
        )
    console.print(table)
    console.print()


def _render_breadth(console: Console, breadth: Optional[Dict]):
    if not breadth:
        return
    z = breadth.get("zt_count")
    d = breadth.get("dt_count")
    s = breadth.get("strong_count")
    mc = breadth.get("max_consecutive")
    if z is None and d is None and s is None:
        return
    console.print(Rule("[bold]一-B、市场宽度（赚钱效应）[/]", style="cyan"))
    bits = []
    if isinstance(z, int): bits.append(f"涨停 [bold red]{z}[/] 家")
    if isinstance(d, int): bits.append(f"跌停 [bold green]{d}[/] 家")
    if isinstance(s, int): bits.append(f"强势 {s} 家")
    if isinstance(mc, int): bits.append(f"最高连板 [bold]{mc}[/] 板")
    console.print(" | ".join(bits))
    top = breadth.get("consecutive_top") or []
    if top:
        rep = ", ".join(f"{n}({b}板)" for n, b in top if n)
        if rep:
            console.print(f"[dim]高度板代表: {rep}[/]")
    console.print()


def _render_margin(console: Console, margin_history: Optional[pd.DataFrame]):
    if margin_history is None or margin_history.empty:
        return
    console.print(Rule("[bold]一-C、沪市两融余额（亿元，近 N 日）[/]", style="cyan"))
    last_n = margin_history.tail(20)
    if len(last_n) >= 2 and "total_balance" in last_n.columns:
        first = float(last_n.iloc[0].get("total_balance", 0) or 0)
        last = float(last_n.iloc[-1].get("total_balance", 0) or 0)
        if first:
            chg = (last - first) / first * 100
            color = "bold red" if chg > 0 else "bold green" if chg < 0 else "dim"
            console.print(
                f"区间变动: {first:.0f} → {last:.0f}  "
                f"[{color}]{chg:+.2f}%[/]"
            )
    last_row = last_n.iloc[-1]
    fb = last_row.get("financing_balance"); sb = last_row.get("short_balance")
    if isinstance(fb, (int, float)) and isinstance(sb, (int, float)):
        console.print(f"[dim]最新结构: 融资 {fb:.0f} 亿 / 融券 {sb:.0f} 亿[/]")
    console.print()


def _render_overseas(console: Console, overseas: Optional[Dict[str, Dict]]):
    if not overseas:
        return
    console.print(Rule("[bold]一-D、海外市场[/]", style="cyan"))
    table = Table(box=SIMPLE)
    table.add_column("指数", style="bold")
    table.add_column("点位", justify="right")
    table.add_column("涨跌幅", justify="right")
    table.add_column("数据日", justify="right", style="dim")
    for name, v in overseas.items():
        price = v.get("price")
        pct = v.get("change_pct")
        d = v.get("date", "")
        if not isinstance(price, (int, float)):
            continue
        table.add_row(
            name,
            f"{price:.2f}",
            _color_pct(pct) if isinstance(pct, (int, float)) else Text("-"),
            str(d)[:10],
        )
    console.print(table)
    console.print()


def _render_etf_premium(console: Console, etf_premium: Optional[Dict[str, Dict]]):
    if not etf_premium:
        return
    console.print(Rule("[bold]三-A、场内 ETF 折溢价[/]", style="cyan"))
    table = Table(box=SIMPLE)
    table.add_column("代码", style="bold")
    table.add_column("名称")
    table.add_column("现价", justify="right")
    table.add_column("涨跌", justify="right")
    table.add_column("溢价率", justify="right")
    for code, v in etf_premium.items():
        if not v:
            continue
        prem = v.get("premium_pct")
        prem_text = "-"
        if isinstance(prem, (int, float)):
            if prem >= 1.5:
                prem_text = Text(f"{prem:+.2f}%", style="bold red")
            elif prem <= -0.5:
                prem_text = Text(f"{prem:+.2f}%", style="bold green")
            else:
                prem_text = Text(f"{prem:+.2f}%", style="dim")
        chg = v.get("change_pct")
        price = v.get("price")
        table.add_row(
            code,
            (v.get("name") or "")[:20],
            f"{price:.3f}" if isinstance(price, (int, float)) else "-",
            _color_pct(chg) if isinstance(chg, (int, float)) else Text("-"),
            prem_text,
        )
    console.print(table)
    console.print()


def _render_sectors(console: Console, sector_data: List[Dict]):
    if not sector_data:
        return
    console.print(Rule("[bold]二、板块资金流向 TOP10[/]", style="cyan"))
    table = Table(box=ROUNDED)
    table.add_column("板块")
    table.add_column("涨跌幅", justify="right")
    table.add_column("主力净流入(亿)", justify="right")

    for row in sector_data[:10]:
        net = row.get("main_net_flow")
        pct = row.get("change_pct")
        net_str = f"{net/1e8:+.2f}" if isinstance(net, (int, float)) else "-"
        net_text = Text(net_str, style="bold red" if isinstance(net, (int, float)) and net > 0 else "bold green")
        table.add_row(
            str(row.get("sector", "-")),
            _color_pct(pct) if isinstance(pct, (int, float)) else Text("-"),
            net_text,
        )
    console.print(table)
    console.print()


def _render_news_summary(console: Console, sentiment: SentimentResult, policy: PolicySignal, news_top: List[Dict]):
    console.print(Rule("[bold]三、消息面 & 政策面[/]", style="cyan"))

    table = Table(box=SIMPLE, show_header=False)
    table.add_column("项目", style="dim", width=12)
    table.add_column("评分", justify="right", width=10)
    table.add_column("摘要")
    table.add_row("消息面情绪", _color_score(sentiment.score), sentiment.summary)
    table.add_row("政策面方向", _color_score(policy.score), f"{policy.direction}：{policy.summary}")
    console.print(table)

    if policy.hits:
        console.print("\n[bold]政策关键命中：[/]")
        for hit in policy.hits[:5]:
            mark = "🟢" if hit["score"] > 0 else "🔴"
            console.print(f"  {mark} [{hit['keywords']}] {hit['title']}")

    if news_top:
        console.print("\n[bold]今日要闻 TOP5：[/]")
        for item in news_top[:5]:
            console.print(f"  • {item.get('title', '')[:80]}")
    console.print()


def _render_funds(console: Console, fund_summaries: List[Dict], decisions: List[Decision]):
    console.print(Rule("[bold]四、持仓基金分析[/]", style="cyan"))
    table = Table(box=ROUNDED)
    table.add_column("代码", style="bold")
    table.add_column("名称")
    table.add_column("最新净值", justify="right")
    table.add_column("盘中估算", justify="right")
    table.add_column("估算涨跌", justify="right")
    table.add_column("近20日", justify="right")
    table.add_column("波动率", justify="right")

    summary_map = {s["code"]: s for s in fund_summaries}
    for d in decisions:
        s = summary_map.get(d.fund_code, {})
        last_nav = s.get("last_nav")
        est_nav = s.get("estimate_nav")
        est_pct = s.get("estimate_pct")
        ch20 = s.get("change_20d")
        vol = s.get("volatility")
        table.add_row(
            d.fund_code,
            d.fund_name[:18],
            f"{last_nav:.4f}" if isinstance(last_nav, (int, float)) else "-",
            f"{est_nav:.4f}" if isinstance(est_nav, (int, float)) else "-",
            _color_pct(est_pct) if isinstance(est_pct, (int, float)) else Text("-"),
            _color_pct(ch20) if isinstance(ch20, (int, float)) else Text("-"),
            f"{vol:.1f}%" if isinstance(vol, (int, float)) else "-",
        )
    console.print(table)
    console.print()


def _render_actions(console: Console, decisions: List[Decision]):
    console.print(Rule("[bold]五、买卖建议（核心）[/]", style="cyan bold"))

    for d in decisions:
        style = ACTION_STYLE.get(d.action, "bold white")
        title = Text.assemble(
            (f" {d.action} ", style),
            (f"  {d.fund_code}  {d.fund_name}", "bold"),
            ("    评分 ", "dim"),
            _color_score(d.score),
            ("    置信度 ", "dim"),
            (d.confidence, "bold"),
        )

        body_lines: List[str] = []
        body_lines.append("[bold]决策依据：[/]")
        for r in d.reasons:
            body_lines.append(f"  • {r}")

        if d.warnings:
            body_lines.append("\n[bold yellow]⚠ 风险提示：[/]")
            for w in d.warnings:
                body_lines.append(f"  • {w}")

        body_lines.append("\n[bold]评分明细：[/]")
        for k, v in d.breakdown.items():
            body_lines.append(f"  {k:<12} {v:+.1f}")

        console.print(Panel(
            "\n".join(body_lines),
            title=title,
            border_style="cyan",
            padding=(1, 2),
        ))
    console.print()


def _render_disclaimer(console: Console):
    console.print(Panel(
        "[dim italic]本报告由量化模型自动生成，所有结论仅供参考，不构成投资建议。\n"
        "投资有风险，决策需谨慎。请结合自身风险承受能力独立判断。[/]",
        border_style="dim",
    ))
