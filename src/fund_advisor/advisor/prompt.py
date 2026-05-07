"""LLM 提示词构建：把量化分析数据整理成结构化中文上下文"""

from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from fund_advisor.analysis.decision import Decision
from fund_advisor.analysis.technical import TechnicalSignal
from fund_advisor.analysis.sentiment import SentimentResult
from fund_advisor.analysis.policy import PolicySignal


SYSTEM_PROMPT = """你是一名**进攻型 A 股基金投资顾问**，擅长趋势右侧加仓、板块轮动与短中线择时。服务对象是**充分信任你判断**、追求超额收益的个人投资者。

【关键交付标准 —— 决定你这份指南是否合格】
- **直接给结论,不要套话**：用户已经决定相信你的判断,**不要写「仅供参考」「请独立判断」「投资有风险」之类的免责套话**。这种内容会让指南失去价值。
- **明确即正确**：每个建议必须给出**可执行的具体动作**（基金代码 + 动作 + 仓位 % + 价位 + 时点）,**不要写「可关注」「值得留意」「灵活调整」这种含糊词**。要写「今日尾盘加仓 3%」「明日跌破 X.XX 减半止损」这种。
- **覆盖三层判断,缺一不可**：
  1. **A 股大盘整体方向** —— 必须明确「看多 / 中性偏多 / 震荡 / 中性偏空 / 看空」五选一,并给目标总仓位 %
  2. **持仓基金逐只** —— 每只都要明确动作 + 目标仓位 + 硬止损 + **明日走势预估** + 加仓触发
  3. **可观望的其他赛道** —— 直接点名 5-8 个赛道 / ETF,**给出具体代码 + 入场信号**,而不是泛泛而谈

【你的投资哲学】
- **顺势而为**：动量与趋势优先,弱者恒弱、强者恒强;不抄底不猜顶,确认了才进
- **满仓优先**：默认目标总仓位 85-95%,把现金视为机会成本而非安全垫;只在系统性风险信号明确时才降仓位
- **单只敢重仓**：单只基金目标仓位上限 20-25%（核心赛道可至 30%）,但绝不超过 35%
- **估值不是禁忌**：景气度 + 趋势 + 资金流 三共振时,估值分位偏高（70-90%）依然可以参与;只有 >90% 极端高估才主动避险
- **快进快出 vs 慢进慢出**：题材热点 → 快进快出;赛道核心 → 趋势确认后慢加慢减
- **止损必须硬**：每只基金都给具体止损位,触发就执行,不抗单

你的专长板块:
- 科技成长：人工智能 / AI 应用 / 半导体 / 存储芯片 / CPO / 光通信 / 5G / 计算机 / 机器人
- 高端制造：电网设备 / 光伏 / 新能源 / 新能源汽车 / 锂电池 / 商业航天 / 国防军工
- 周期资源：有色金属 / 工业金属 / 稀土 / 锂矿 / 油气 / 石油石化
- 宽基与红利：上证科创50 / 沪深300 / 改革红利 / 国企
- 被动指数与 ETF 联接基金（场内 ETF 是进攻型的主力工具）

【数据说明 — 你必须正确理解输入中的量化字段】
- "综合评分"：本地量化模型对该基金的综合打分（范围约 -100 ~ +100）,**进攻型已调整阈值**：>=25 即"买入",>=5 即"加仓",-25~5 为"持有"。**最终判断以你为准** —— 你不需要解释「为什么覆盖量化结论」,直接给你的判断即可。
- "置信度（高/中/低）"：信号一致性。**进攻型用法：高置信度顶格仓位、中置信度核心仓位、低置信度减半但不空仓 —— 用最强趋势的标的承接**。
- "评分明细"：评分拆分项。基金技术面与板块归因占比最大,这两项强即进攻型的核心买点。
- "估值分位"：指数 PE/PB 在过去 5 年 / 10 年的相对水位（0-100%）。**进攻型解读：< 30% 抄底机会,30-70% 完全可参与,70-90% 顺势加仓也无妨,> 90% 才需警惕回归**。
- "前 10 大重仓股 / 当日归因"：基金披露的重仓股 + 当日涨跌 + 权重×涨跌的归因贡献。**归因贡献 > +1.5 是顺势加仓的强信号**;< -1.5 且持续两日以上才考虑减仓。
- "两融余额"：境内杠杆资金的核心信号。20 日上行 5% 以上 → 进攻信号;下行 > 5% 才考虑收手。
- "市场宽度"：涨停 ≥ 80 + 高度板 ≥ 5 → 强情绪,可追题材;涨停 < 30 + 跌停 > 涨停 → 退守核心赛道,不碰题材。
- "海外指数"：纳指 / 港股科技对 A 股科技板块次日开盘有显著联动,可作为加仓择时的辅助信号。
- "场内 ETF 折溢价"：**溢价 > +1.5% 必须警示场内追涨风险**,改用场外联接基金或等回归;折价 < -0.5% 反而是场内进攻型的优选入口。

【输出结构 —— 严格按此六段交付】
## 一、A 股大盘判断
- **方向**：看多 / 中性偏多 / 震荡 / 中性偏空 / 看空（五选一,直接写结论）
- **依据**：3-5 条,引用 30 日趋势 + 北向 + 两融 + 宽度 + 估值的具体数字
- **总仓位建议**：明确百分比（85% / 70% 等）
- **风格倾向**：成长 / 价值 / 平衡 / 防御（直接写）

## 二、持仓基金逐只分析（最重要,篇幅最大）
每只基金严格按 `### 基金代码 名称` 三级标题,下设四个加粗子段:
- **【当天建议操作】**：动作 + 目标仓位 %（核心 20-25% / 卫星 10-15%） + 执行节奏 + **硬止损位**（具体净值 + 止损 %,例如 -7%）
- **【明日走势预估】**：结合**大盘数据（含海外夜盘）+ 消息面 + 政策面 + 重仓股当日归因**,给出次日(T+1)明确判断:
  - **方向**（大涨 / 上涨 / 震荡 / 下跌 / 大跌,五选一,直接写结论）
  - **预估涨跌区间**(具体百分比,例如 +0.5% ~ +1.5%)
  - **三条关键依据**(夜盘纳指/港股、政策日历、消息面情绪、相关板块次日资金预期、重仓股是否有利空/利好事件)
  - **开盘应对**(高开 X% 减仓 / 低开 Y% 加仓 / 平开观察)
- **【后续走势预估（1-2 周 / 1 个月）】**：趋势性方向 + 关键支撑/阻力位 + 趋势依据（30 日净值轨迹 + 板块资金 + 估值分位 + 重仓股归因）
- **【加仓触发】**：「如果 X 发生 → 加仓 Y%」,至少 1-2 条可量化条件

## 三、可观望的其他赛道（5-8 条,这是用户最看重的输出之一）
不限于持仓主题。每条按以下格式:
- **赛道名 + 代表性 ETF/联接基金代码 + 一句话核心逻辑 + 入场信号**
- 例：`半导体设备 / 159516 半导体设备 ETF / AI 资本开支景气持续,设备端利润最先兑现 / 站稳 1.05 元 + 板块单日净流入 > 10 亿 → 5% 仓位试仓`

## 四、主动加仓机会（核心进攻动作）
从持仓 + 观察池中挑出今日就有强信号的标的（综合评分 > 25 + 板块归因 > +1.0 + 板块净流入 > 10 亿 等共振 ≥ 2 项）,直接写「今日尾盘 X 代码加仓 Y%,止损 Z」。**没有就明确写「今日无新增加仓机会,维持原仓位」,不要硬凑**。

## 五、下一交易日关键观察点（3-5 条）
每条必须是「可量化信号 → 触发动作」:例如「若上证跌破 3380 → 持仓基金减半」「若 AI 板块净流入 > 30 亿 → 加仓 510050」。

## 六、止损纪律（1-2 行,不超过两行）
只写硬止损规则,不写常规风险提示。例:「单只触发 -7% 立即减半,触发 -10% 清仓;总仓位回撤 > 5% 整体降到 60%」。

【最终硬约束】
- 全程中文,Markdown 标题列表
- **不写「投资有风险,决策需谨慎」「仅供参考」「请独立判断」**
- 不写「可关注」「值得留意」「灵活调整」「相机决策」等含糊词;一律用动作动词 + 具体数字
- 推荐基金时优先 ETF 或联接基金代码（场外联接代码避免溢价风险）
- 总篇幅 3500-6000 字（基金多时可延长,但宁可详细不要含糊）
"""


# ---- 当日大盘 ---------------------------------------------------------------

def _format_market(market_data: Dict, market_tech: TechnicalSignal, north_money: float) -> str:
    lines = ["## 一、大盘当日行情"]
    if market_data:
        for name, info in market_data.items():
            price = info.get("price")
            pct = info.get("change_pct")
            amt = info.get("amount")
            row = f"- **{name}**:"
            if isinstance(price, (int, float)):
                row += f" {price:.2f}"
            if isinstance(pct, (int, float)):
                row += f" ({pct:+.2f}%)"
            if isinstance(amt, (int, float)):
                row += f"，成交 {amt/1e8:.0f} 亿"
            lines.append(row)
    else:
        lines.append("- (大盘数据缺失)")

    lines += ["", "### 大盘技术面（基于上证 180 日）"]
    lines.append(f"- 趋势判断：{market_tech.trend}")
    lines.append(f"- 均线：{market_tech.ma_signal}")
    if isinstance(market_tech.details.get("ma5"), (int, float)):
        lines.append(
            f"- MA5/20/60：{market_tech.details['ma5']:.2f} / "
            f"{market_tech.details.get('ma20', 0):.2f} / "
            f"{market_tech.details.get('ma60', 0):.2f}"
        )
    lines.append(f"- MACD：{market_tech.macd_signal}")
    if isinstance(market_tech.rsi_value, (int, float)):
        lines.append(f"- RSI：{market_tech.rsi_value:.1f}（{market_tech.rsi_signal}）")
    if isinstance(north_money, (int, float)) and north_money:
        lines.append(f"- 北向资金当日：{north_money:+.1f} 亿")
    lines.append(f"- 综合技术评分：{market_tech.score:+.1f}")
    return "\n".join(lines)


# ---- 30 日大盘日线 ----------------------------------------------------------

def _format_index_history(index_histories: Optional[Dict[str, pd.DataFrame]]) -> str:
    if not index_histories:
        return ""
    lines = ["## 二、近 30 日大盘日线"]
    for name, df in index_histories.items():
        if df is None or df.empty:
            continue
        lines.append("")
        lines.append(f"### {name}")
        # 紧凑表格: 日期 收盘 涨跌% 成交亿
        head = "| 日期 | 收盘 | 涨跌% | 成交亿 |\n|---|---:|---:|---:|"
        lines.append(head)
        rows: List[str] = []
        for _, r in df.iterrows():
            d = r.get("date")
            try:
                d_str = pd.Timestamp(d).strftime("%m-%d") if d is not None else "-"
            except Exception:
                d_str = str(d)[:10]
            close = r.get("close")
            pct = r.get("change_pct")
            amt = r.get("amount")
            close_s = f"{close:.2f}" if isinstance(close, (int, float)) else "-"
            pct_s = f"{pct:+.2f}" if isinstance(pct, (int, float)) else "-"
            amt_s = f"{amt/1e8:.0f}" if isinstance(amt, (int, float)) else "-"
            rows.append(f"| {d_str} | {close_s} | {pct_s} | {amt_s} |")
        lines.extend(rows)

        # 区间统计
        if "close" in df.columns and not df["close"].dropna().empty:
            highs = df["close"].astype(float)
            high_idx = highs.idxmax()
            low_idx = highs.idxmin()
            first = float(highs.iloc[0])
            last = float(highs.iloc[-1])
            cum = (last / first - 1) * 100 if first else 0
            try:
                high_d = pd.Timestamp(df.loc[high_idx, "date"]).strftime("%m-%d")
                low_d = pd.Timestamp(df.loc[low_idx, "date"]).strftime("%m-%d")
            except Exception:
                high_d = low_d = "-"
            lines.append(
                f"- 30日内: 最高 {highs.max():.2f}（{high_d}）, "
                f"最低 {highs.min():.2f}（{low_d}）, 累计 {cum:+.2f}%"
            )
    return "\n".join(lines)


# ---- 北向资金近 10 日趋势 --------------------------------------------------

def _format_north_history(north_money: float, north_history: Optional[pd.DataFrame]) -> str:
    if (north_history is None or north_history.empty) and not north_money:
        return ""
    lines = ["## 三、北向资金"]
    if isinstance(north_money, (int, float)) and north_money:
        lines.append(f"- 当日净流入：**{north_money:+.1f} 亿**")
    if north_history is not None and not north_history.empty:
        seq: List[str] = []
        for _, r in north_history.iterrows():
            try:
                d = pd.Timestamp(r["date"]).strftime("%m-%d")
            except Exception:
                d = "-"
            v = r.get("north_net_flow")
            if isinstance(v, (int, float)):
                seq.append(f"{d} {v:+.1f}")
        if seq:
            lines.append(f"- 近 {len(seq)} 日轨迹（亿）：{'; '.join(seq)}")
            try:
                total = float(north_history["north_net_flow"].sum())
                lines.append(f"- 近 {len(seq)} 日累计：{total:+.1f} 亿")
            except Exception:
                pass
    return "\n".join(lines)


# ---- 估值分位 ---------------------------------------------------------------

def _format_valuations(valuations: Optional[Dict[str, Dict]]) -> str:
    if not valuations:
        return ""
    lines = ["## 三-A、指数估值分位（PE/PB 历史水位）"]
    lines.append("> 分位 < 30% 偏便宜，30-70% 中性，> 70% 偏贵；同时给出 5 年 / 10 年两个窗口")
    for name, v in valuations.items():
        parts: List[str] = [f"- **{name}**"]
        if isinstance(v.get("pe"), (int, float)):
            seg = f"PE {v['pe']:.2f}"
            p5 = v.get("pe_pct_5y"); p10 = v.get("pe_pct_10y")
            if isinstance(p5, (int, float)) or isinstance(p10, (int, float)):
                bits = []
                if isinstance(p5, (int, float)): bits.append(f"5Y分位 {p5:.0f}%")
                if isinstance(p10, (int, float)): bits.append(f"10Y分位 {p10:.0f}%")
                seg += f"（{' / '.join(bits)}）"
            parts.append(seg)
        if isinstance(v.get("pb"), (int, float)):
            seg = f"PB {v['pb']:.2f}"
            p5 = v.get("pb_pct_5y"); p10 = v.get("pb_pct_10y")
            if isinstance(p5, (int, float)) or isinstance(p10, (int, float)):
                bits = []
                if isinstance(p5, (int, float)): bits.append(f"5Y分位 {p5:.0f}%")
                if isinstance(p10, (int, float)): bits.append(f"10Y分位 {p10:.0f}%")
                seg += f"（{' / '.join(bits)}）"
            parts.append(seg)
        if v.get("date"):
            parts.append(f"截至 {v['date']}")
        lines.append("：" .join([parts[0], "，".join(parts[1:])]) if len(parts) > 1 else parts[0])
    return "\n".join(lines)


# ---- 两融余额 ---------------------------------------------------------------

def _format_margin(margin_history: Optional[pd.DataFrame]) -> str:
    if margin_history is None or margin_history.empty:
        return ""
    lines = ["## 三-B、沪市两融余额（亿元，近 N 日）"]
    last_n = margin_history.tail(20)
    seq: List[str] = []
    for _, r in last_n.iterrows():
        try:
            d = pd.Timestamp(r["date"]).strftime("%m-%d")
        except Exception:
            d = "-"
        tot = r.get("total_balance")
        if isinstance(tot, (int, float)):
            seq.append(f"{d} {tot:.0f}")
    if seq:
        lines.append(f"- 两融余额轨迹：{'; '.join(seq)}")

    if len(last_n) >= 2:
        first = float(last_n.iloc[0].get("total_balance", 0) or 0)
        last = float(last_n.iloc[-1].get("total_balance", 0) or 0)
        if first:
            chg = (last - first) / first * 100
            lines.append(f"- 区间变动：{first:.0f} → {last:.0f}（{chg:+.2f}%）")

    last_row = last_n.iloc[-1]
    fb = last_row.get("financing_balance"); sb = last_row.get("short_balance")
    if isinstance(fb, (int, float)) and isinstance(sb, (int, float)):
        lines.append(f"- 最新结构：融资 {fb:.0f} 亿 / 融券 {sb:.0f} 亿")
    return "\n".join(lines)


# ---- 市场宽度 ---------------------------------------------------------------

def _format_breadth(breadth: Optional[Dict]) -> str:
    if not breadth:
        return ""
    z = breadth.get("zt_count"); d = breadth.get("dt_count")
    s = breadth.get("strong_count"); mc = breadth.get("max_consecutive")
    if z is None and d is None and s is None:
        return ""
    lines = ["## 三-C、市场宽度（赚钱效应）"]
    parts: List[str] = []
    if isinstance(z, int): parts.append(f"涨停 {z} 家")
    if isinstance(d, int): parts.append(f"跌停 {d} 家")
    if isinstance(s, int): parts.append(f"强势股 {s} 家")
    if isinstance(mc, int): parts.append(f"最高连板 {mc} 板")
    if parts:
        lines.append("- " + " / ".join(parts))
    top = breadth.get("consecutive_top") or []
    if top:
        seq = [f"{n}({b}板)" for n, b in top if n]
        if seq:
            lines.append(f"- 高度板代表：{', '.join(seq)}")
    return "\n".join(lines)


# ---- 海外联动 ---------------------------------------------------------------

def _format_overseas(overseas: Optional[Dict[str, Dict]]) -> str:
    if not overseas:
        return ""
    lines = ["## 三-D、海外市场（昨夜 / 最新）"]
    for name, v in overseas.items():
        price = v.get("price"); pct = v.get("change_pct"); d = v.get("date", "")
        if not isinstance(price, (int, float)):
            continue
        line = f"- **{name}**：{price:.2f}"
        if isinstance(pct, (int, float)):
            line += f"（{pct:+.2f}%）"
        if d:
            line += f"  [{d}]"
        lines.append(line)
    return "\n".join(lines)


# ---- 场内 ETF 折溢价 --------------------------------------------------------

def _format_etf_premium(etf_premium: Optional[Dict[str, Dict]]) -> str:
    if not etf_premium:
        return ""
    lines = [
        "## 三-E、场内 ETF 折溢价",
        "> 溢价率 > +1.5% 通常意味场内追涨,有回归风险; < -1.0% 折价时场内便宜,可考虑场内买入。",
    ]
    for code, v in etf_premium.items():
        if not v:
            continue
        name = v.get("name") or code
        prem = v.get("premium_pct")
        chg = v.get("change_pct")
        price = v.get("price")
        parts = [f"- **{code}** {name}"]
        if isinstance(price, (int, float)):
            parts.append(f"现价 {price:.3f}")
        if isinstance(chg, (int, float)):
            parts.append(f"涨跌 {chg:+.2f}%")
        if isinstance(prem, (int, float)):
            tag = "高溢价⚠️" if prem >= 1.5 else ("折价" if prem <= -0.5 else "正常")
            parts.append(f"溢价率 {prem:+.2f}% [{tag}]")
        lines.append("：".join([parts[0], "，".join(parts[1:])]) if len(parts) > 1 else parts[0])
    return "\n".join(lines)


# ---- 数据健康状况 -----------------------------------------------------------

def _format_health(health: Optional[Dict[str, str]]) -> str:
    """暴露当次数据采集的成败,提醒 LLM 哪些维度不可用."""
    if not health:
        return ""
    failed = [k for k, v in health.items() if v == "failed"]
    empty = [k for k, v in health.items() if v == "empty"]
    if not failed and not empty:
        return ""
    lines = ["## 数据健康状况(本次未取到的字段,**请勿对其做推断**)"]
    if failed:
        lines.append(f"- 失败: {', '.join(failed)}")
    if empty:
        lines.append(f"- 空数据: {', '.join(empty)}")
    return "\n".join(lines)


# ---- 板块资金流 -------------------------------------------------------------

def _format_sectors(sector_data: List[Dict]) -> str:
    if not sector_data:
        return ""
    lines = ["## 四、板块资金流向 TOP10"]
    for row in sector_data[:10]:
        sec = row.get("sector", "-")
        pct = row.get("change_pct")
        net = row.get("main_net_flow")
        line = f"- {sec}："
        if isinstance(pct, (int, float)):
            line += f"涨跌 {pct:+.2f}%"
        if isinstance(net, (int, float)):
            line += f"，主力净流入 {net/1e8:+.2f} 亿"
        lines.append(line)
    return "\n".join(lines)


# ---- 消息面 & 政策面 + 7 日新闻时间线 --------------------------------------

def _format_news_block(
    sentiment: SentimentResult,
    policy: PolicySignal,
    news_top: List[Dict],
    policy_news_recent: Optional[List[Dict]],
) -> str:
    lines = ["## 五、消息面 & 政策面（基于近 7 日全量）"]
    lines.append(f"- 消息面情绪评分：{sentiment.score:+.1f}")
    if sentiment.summary:
        lines.append(f"  - {sentiment.summary}")
    lines.append(f"- 政策面方向：{policy.direction}（评分 {policy.score:+.1f}）")
    if policy.summary:
        lines.append(f"  - {policy.summary}")

    if policy.hits:
        lines += ["", "### 政策关键命中（按影响排序）"]
        for hit in policy.hits[:8]:
            score_v = hit.get("score", 0) if isinstance(hit, dict) else getattr(hit, "score", 0)
            kw = hit.get("keywords", "") if isinstance(hit, dict) else getattr(hit, "keywords", "")
            title = hit.get("title", "") if isinstance(hit, dict) else getattr(hit, "title", "")
            t = hit.get("time", "") if isinstance(hit, dict) else getattr(hit, "time", "")
            mark = "🟢" if score_v > 0 else "🔴"
            lines.append(f"- {mark} [{kw}] {title} {f'({t})' if t else ''}")

    if policy_news_recent:
        lines += ["", "### 近 7 日政策事件时间线（最多 40 条）"]
        for item in policy_news_recent[:40]:
            ts = item.get("_ts") or item.get("time", "")
            kw = item.get("_keywords", "")
            title = (item.get("title") or "").strip()
            if not title:
                continue
            lines.append(f"- `{ts}` [{kw}] {title[:120]}")

    if news_top:
        lines += ["", "### 近 7 日财经要闻 TOP30"]
        for item in news_top[:30]:
            ts = item.get("_ts") or item.get("time", "")
            title = (item.get("title") or "").strip()
            if not title:
                continue
            lines.append(f"- `{ts}` {title[:120]}")
    return "\n".join(lines)


# ---- 持仓基金 + 30 日 NAV 轨迹 ---------------------------------------------

def _format_fund_history(history: pd.DataFrame) -> str:
    if history is None or history.empty:
        return ""
    parts: List[str] = []
    for _, r in history.iterrows():
        try:
            d = pd.Timestamp(r["date"]).strftime("%m-%d")
        except Exception:
            d = "-"
        nav = r.get("nav")
        pct = r.get("change_pct")
        nav_s = f"{nav:.4f}" if isinstance(nav, (int, float)) else "-"
        pct_s = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        parts.append(f"{d} {nav_s} {pct_s}".strip())
    return "; ".join(parts)


def _format_holdings_block(holdings_data: Optional[Dict]) -> List[str]:
    """单只基金的穿透持仓 + 行业配置 + 重仓股当日归因"""
    if not holdings_data:
        return []
    out: List[str] = []

    industries = holdings_data.get("industries")
    if industries is not None and not industries.empty:
        bits: List[str] = []
        for _, r in industries.iterrows():
            ind = str(r.get("industry", "")).strip()
            w = r.get("weight")
            if ind and isinstance(w, (int, float)):
                bits.append(f"{ind} {w:.1f}%")
        if bits:
            out.append(f"- 行业配置（最新季报）：{' / '.join(bits[:6])}")

    top_holdings = holdings_data.get("top_holdings")
    attribution = holdings_data.get("attribution") or []
    attr_map = {a.get("stock_code"): a for a in attribution if a.get("stock_code")}

    if top_holdings is not None and not top_holdings.empty:
        out.append("- 前 10 大重仓股（含当日涨跌归因）：")
        out.append("")
        out.append("  | 股票 | 权重% | 当日涨跌% | 贡献(权重×涨跌) |")
        out.append("  |---|---:|---:|---:|")
        total_contrib = 0.0
        any_attr = False
        for _, r in top_holdings.iterrows():
            sc = str(r.get("stock_code", "")).zfill(6)
            sn = str(r.get("stock_name", ""))
            w = r.get("weight")
            attr = attr_map.get(sc, {})
            chg = attr.get("change_pct")
            contrib = attr.get("contribution")
            w_s = f"{w:.2f}" if isinstance(w, (int, float)) else "-"
            chg_s = f"{chg:+.2f}" if isinstance(chg, (int, float)) else "-"
            contrib_s = f"{contrib:+.3f}" if isinstance(contrib, (int, float)) else "-"
            out.append(f"  | {sn}({sc}) | {w_s} | {chg_s} | {contrib_s} |")
            if isinstance(contrib, (int, float)):
                total_contrib += contrib
                any_attr = True

        if any_attr:
            out.append(f"  - 重仓股加总贡献（仅披露权重×当日涨跌）：**{total_contrib:+.2f}%**")

        # 上涨 / 下跌龙头
        ups = sorted(
            [a for a in attribution if isinstance(a.get("change_pct"), (int, float))],
            key=lambda x: x["change_pct"], reverse=True,
        )
        if ups:
            top3 = ups[:3]; bot3 = ups[-3:]
            out.append(
                "  - 当日领涨重仓：" +
                "; ".join(f"{a.get('stock_name','')}({a['change_pct']:+.2f}%)" for a in top3 if a.get('stock_name'))
            )
            if len(ups) > 3:
                out.append(
                    "  - 当日领跌重仓：" +
                    "; ".join(f"{a.get('stock_name','')}({a['change_pct']:+.2f}%)" for a in bot3 if a.get('stock_name'))
                )

        # 标注披露季度
        if "quarter" in top_holdings.columns and not top_holdings["quarter"].empty:
            q = top_holdings["quarter"].iloc[0]
            out.append(f"  - 持仓披露口径：{q}（季报有滞后）")

    return out


def _format_funds(
    fund_summaries: List[Dict],
    decisions: List[Decision],
    fund_histories: Optional[Dict[str, pd.DataFrame]],
    fund_holdings: Optional[Dict[str, Dict]] = None,
) -> str:
    if not decisions:
        return ""
    summary_map = {s["code"]: s for s in fund_summaries}
    lines = ["## 六、持仓基金量化分析"]

    for d in decisions:
        s = summary_map.get(d.fund_code, {})
        lines.append("")
        lines.append(f"### {d.fund_code} {d.fund_name}")
        if s.get("theme"):
            lines.append(f"- 主题：{s['theme']}")
        if isinstance(s.get("last_nav"), (int, float)):
            lines.append(f"- 最新单位净值：{s['last_nav']:.4f}")
        if isinstance(s.get("estimate_nav"), (int, float)):
            est_pct = s.get("estimate_pct")
            est_pct_str = f" ({est_pct:+.2f}%)" if isinstance(est_pct, (int, float)) else ""
            lines.append(f"- 盘中估算净值：{s['estimate_nav']:.4f}{est_pct_str}")
        if isinstance(s.get("change_20d"), (int, float)):
            lines.append(f"- 近 20 日累计涨跌：{s['change_20d']:+.2f}%")
        if isinstance(s.get("volatility"), (int, float)):
            lines.append(f"- 年化波动率：{s['volatility']:.1f}%")
        if isinstance(s.get("max_drawdown"), (int, float)):
            lines.append(f"- 最大回撤：{s['max_drawdown']:.1f}%")
        lines.append(
            f"- 量化模型建议：**{d.action}**（综合评分 {d.score:+.1f}，置信度 {d.confidence}）"
        )

        if d.reasons:
            lines.append("- 决策依据：")
            for r in d.reasons:
                lines.append(f"  - {r}")
        if d.warnings:
            lines.append("- 风险提示：")
            for w in d.warnings:
                lines.append(f"  - {w}")
        if d.breakdown:
            details = "，".join(f"{k} {v:+.1f}" for k, v in d.breakdown.items())
            lines.append(f"- 评分明细：{details}")

        if fund_histories:
            hist = fund_histories.get(d.fund_code)
            if hist is not None and not hist.empty:
                trace = _format_fund_history(hist)
                if trace:
                    lines.append(f"- 近 30 日净值轨迹：{trace}")

        # 穿透持仓 + 当日归因
        if fund_holdings:
            block = _format_holdings_block(fund_holdings.get(d.fund_code))
            lines.extend(block)

    return "\n".join(lines)


# ---- 主入口 -----------------------------------------------------------------

def build_user_prompt(
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
    index_histories: Optional[Dict[str, pd.DataFrame]] = None,
    fund_histories: Optional[Dict[str, pd.DataFrame]] = None,
    north_history: Optional[pd.DataFrame] = None,
    policy_news_recent: Optional[List[Dict]] = None,
    margin_history: Optional[pd.DataFrame] = None,
    breadth: Optional[Dict] = None,
    overseas: Optional[Dict[str, Dict]] = None,
    valuations: Optional[Dict[str, Dict]] = None,
    fund_holdings: Optional[Dict[str, Dict]] = None,
    etf_premium: Optional[Dict[str, Dict]] = None,
    data_health: Optional[Dict[str, str]] = None,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f"# A 股基金当日量化分析数据（{today}）",
        "",
        _format_health(data_health),
        "",
        _format_market(market_data, market_tech, north_money),
        "",
        _format_index_history(index_histories),
        "",
        _format_north_history(north_money, north_history),
        "",
        _format_valuations(valuations),
        "",
        _format_margin(margin_history),
        "",
        _format_breadth(breadth),
        "",
        _format_overseas(overseas),
        "",
        _format_etf_premium(etf_premium),
        "",
        _format_sectors(sector_data),
        "",
        _format_news_block(sentiment, policy, news_top, policy_news_recent),
        "",
        _format_funds(fund_summaries, decisions, fund_histories, fund_holdings),
        "",
        "---",
        "",
        "请基于以上数据（30 日大盘走势 + 7 日政策时间线 + 各基金 30 日净值轨迹 + 估值分位 + 重仓股当日归因 + 场内 ETF 折溢价），按**进攻型**风格输出今日操作指南。**用户充分信任你的判断,要求明确、可执行、不含糊**。严格按以下六段交付:",
        "",
        "## 一、A 股大盘判断",
        "- **方向**：在「看多 / 中性偏多 / 震荡 / 中性偏空 / 看空」中**直接选一**,写在第一行,不要含糊",
        "- **依据**：3-5 条,引用具体数字(30 日大盘累计涨跌、北向 5 日累计、两融 20 日变动 %、涨停/跌停数、估值分位)",
        "- **总仓位建议**：直接给百分比（例如 90% / 70%）,并说明现金的用途",
        "- **风格倾向**：成长 / 价值 / 平衡 / 防御（直接写一个）",
        "",
        "## 二、持仓基金逐只分析（最重要,篇幅最大）",
        "- 每只基金严格按 `### 基金代码 名称` 三级标题展开",
        "- 下设四个加粗子段,**四段都必须写**：",
        "    - **【当天建议操作】**：动作（买入/加仓/持有/减仓/卖出）+ 目标仓位 %（核心 20-25% / 卫星 10-15%）+ 执行节奏（一次性 / 分 N 日均买）+ **硬止损位**（具体净值 + 止损 %,例如 -7%）",
        "    - **【明日走势预估】**（结合**大盘数据 + 消息面 + 政策面 + 重仓股归因**给出次日 T+1 判断）:",
        "        - **方向**: 大涨 / 上涨 / 震荡 / 下跌 / 大跌（五选一,直接写结论）",
        "        - **预估涨跌区间**: 具体百分比,例如 +0.5% ~ +1.5% 或 -1% ~ 0%",
        "        - **三条关键依据**: 必须至少涵盖以下两类 —— 夜盘纳指/港股科技联动、次日政策日历或要闻催化、相关板块次日资金预期、重仓股利好/利空事件、大盘技术位是否临界",
        "        - **开盘应对**: 高开 X% 减仓 / 低开 Y% 加仓 / 平开观察;写出具体阈值",
        "    - **【后续走势预估（1-2 周 / 1 个月）】**: 趋势性方向,引用 30 日净值轨迹 + 板块资金 + 估值分位 + 重仓股归因;明确**关键支撑位 / 阻力位**",
        "    - **【加仓触发】**: 「如果 X 发生 → 加仓 Y%」,至少 1-2 条可量化条件（站稳 X 元加 3% / 板块净流入 > 15 亿加 5% / 归因连续两日 > +1.0 加 2%）",
        "- 引用数据点时务必带具体数字",
        "- 估值分位 70-90% 不是禁区,只在 > 90% 时提示「极端高估,加仓需控制单次比例」",
        "- 置信度低时不空仓,减半仓位 + 用最强趋势的标的承接",
        "- 场内 ETF 溢价 > +1.5% 改场外联接;折价 < -0.5% 视为进攻型入口",
        "- 你的判断与上文「量化模型建议」不一致时,直接给你的判断,不需要解释「为什么覆盖」",
        "",
        "## 三、可观望的其他赛道（5-8 条,这是用户最看重的输出之一）",
        "- 不限于持仓主题。每条按以下格式逐行写:",
        "    `**赛道名 / ETF 代码（场外联接代码） / 核心逻辑（一句话） / 入场信号（可量化）**`",
        "- 例:`半导体设备 / 159516（场外 008888） / AI 资本开支景气持续,设备端利润最先兑现 / 站稳 1.05 元 + 板块单日净流入 > 10 亿 → 试仓 5%`",
        "- 必须给具体 ETF 代码(用真实存在的代码),不要写「可关注半导体方向」这种含糊话",
        "- 入场信号必须是可量化的「价位 + 资金/技术信号」,不是「逢低布局」",
        "- 涵盖至少 2-3 个不同板块（避免全在科技或全在周期）,体现板块轮动的进攻型布局",
        "",
        "## 四、主动加仓机会（核心进攻动作）",
        "从持仓 + 观察池中挑出今日就有强信号的标的（综合评分 > 25 + 板块归因 > +1.0 + 板块净流入 > 10 亿 等共振 ≥ 2 项）,直接写「今日尾盘 X 代码加仓 Y%,止损 Z 元」。如果今日确实没有共振机会,**直接写一行「今日无新增加仓机会,维持原仓位」**,不要硬凑。",
        "",
        "## 五、下一交易日关键观察点（3-5 条）",
        "每条必须是「可量化信号 → 触发动作」格式:",
        "- 例:「若上证跌破 3380 → 持仓基金减半」「若 AI 板块净流入 > 30 亿 → 加仓 159516 至 8%」「若纳指夜盘大涨 > 2% → 次日开盘加仓科技 ETF 3%」",
        "",
        "## 六、止损纪律（最多两行）",
        "只写硬止损规则,例:「单只触发 -7% 立即减半,触发 -10% 清仓;总仓位回撤 > 5% 整体降到 60%」。**不要写「投资有风险」「仅供参考」「请独立判断」「投资需谨慎」之类的免责套话**。",
    ]
    return "\n".join(p for p in parts if p)
