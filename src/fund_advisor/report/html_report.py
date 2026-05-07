"""HTML 报告生成"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from jinja2 import Template

from fund_advisor.analysis.decision import Decision
from fund_advisor.analysis.technical import TechnicalSignal
from fund_advisor.analysis.sentiment import SentimentResult
from fund_advisor.analysis.policy import PolicySignal


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>A 股基金每日分析报告 · {{ date }}</title>
<style>
* { box-sizing: border-box; }
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       max-width: 1200px; margin: 0 auto; padding: 0 20px 60px; color: #222;
       background: #fafafa; }
header.report-head { padding: 28px 0 12px; }
h1 { color: #c0392b; margin: 0; }
.report-meta { color: #888; font-size: 13px; margin-top: 4px; }

/* === Health badge banner === */
.health-banner { background: #fff8e1; border: 1px solid #f4d35e; border-radius: 6px;
                 padding: 8px 14px; margin: 10px 0; font-size: 13px; color: #604300; }
.health-banner .label { font-weight: 600; margin-right: 8px; }
.health-banner .tag { display: inline-block; padding: 1px 8px; border-radius: 10px;
                      font-size: 12px; margin: 0 3px; }
.health-banner .tag-failed { background: #f8d7da; color: #842029; }
.health-banner .tag-empty  { background: #fff3cd; color: #664d03; }

/* === Tab nav === */
.tab-nav { position: sticky; top: 0; z-index: 50; background: #fafafa;
           padding: 14px 0 10px; border-bottom: 2px solid #e8e8e8;
           display: flex; flex-wrap: wrap; gap: 8px; }
.tab-nav button { padding: 8px 16px; border: 1px solid #ddd; border-radius: 20px;
                  background: #fff; cursor: pointer; font-size: 13px; color: #444;
                  transition: all 0.15s ease; font-family: inherit; }
.tab-nav button:hover { border-color: #c0392b; color: #c0392b; }
.tab-nav button.active { background: #c0392b; color: #fff; border-color: #c0392b;
                         box-shadow: 0 2px 6px rgba(192, 57, 43, 0.25); }
.tab-nav button.ai-tab { background: #2c3e50; color: #fff; border-color: #2c3e50; }
.tab-nav button.ai-tab.active { background: #1a252f; box-shadow: 0 2px 8px rgba(44, 62, 80, 0.4); }

/* === Tab panes === */
.tab-pane { display: none; padding: 20px 0; animation: fadeIn 0.2s ease; }
.tab-pane.active { display: block; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
.tab-pane h2 { border-left: 4px solid #c0392b; padding-left: 12px; margin-top: 8px;
               font-size: 22px; }
.tab-pane h3 { margin-top: 24px; color: #444; }

/* === Generic === */
table { width: 100%; border-collapse: collapse; margin: 12px 0; background: #fff;
        border-radius: 6px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
th, td { padding: 10px 14px; border-bottom: 1px solid #eee; text-align: left; }
th { background: #f7f7f7; font-weight: 600; }
.up { color: #c0392b; font-weight: 600; }
.down { color: #16a085; font-weight: 600; }
.flat { color: #777; }
.score-pos { color: #c0392b; font-weight: 700; }
.score-neg { color: #16a085; font-weight: 700; }
.score-mid { color: #d68910; font-weight: 700; }
.action { padding: 4px 10px; border-radius: 4px; font-weight: 700; color: white; }
.action-buy { background: #c0392b; }
.action-add { background: #e67e22; }
.action-hold { background: #f1c40f; color: #222; }
.action-reduce { background: #27ae60; }
.action-sell { background: #16a085; }

/* === Quantile bar (估值分位) === */
.qbar { display: inline-block; vertical-align: middle; width: 100px; height: 8px;
        background: linear-gradient(to right, #16a085 0%, #f1c40f 50%, #c0392b 100%);
        border-radius: 4px; position: relative; margin-right: 8px; }
.qbar .marker { position: absolute; top: -3px; width: 2px; height: 14px;
                background: #222; border-radius: 1px; }
.qbar-text { font-size: 12px; color: #555; }
.q-low { color: #16a085; font-weight: 600; }
.q-mid { color: #d68910; font-weight: 600; }
.q-high { color: #c0392b; font-weight: 600; }

/* === Premium badge === */
.prem-high { color: #c0392b; font-weight: 600; }
.prem-low  { color: #16a085; font-weight: 600; }
.prem-mid  { color: #777; }

/* === Fund card 折叠 === */
.fund-card { border: 1px solid #e6e6e6; border-radius: 8px; margin: 10px 0;
             background: #fff; overflow: hidden; }
.fund-card summary { cursor: pointer; padding: 14px 18px; list-style: none;
                     display: flex; align-items: center; justify-content: space-between;
                     gap: 12px; font-weight: 600; user-select: none; }
.fund-card summary::-webkit-details-marker { display: none; }
.fund-card summary::after { content: "▾"; color: #999; transition: transform 0.2s; }
.fund-card[open] summary::after { transform: rotate(180deg); }
.fund-card summary:hover { background: #fafafa; }
.fund-card .card-title { display: flex; align-items: center; gap: 10px; flex: 1;
                         font-size: 15px; }
.fund-card .card-meta { color: #888; font-weight: normal; font-size: 12px; }
.fund-card .card-body { padding: 4px 18px 16px; border-top: 1px solid #f0f0f0; }
.reasons li { margin: 4px 0; }
.warning { color: #c0392b; }

/* === Filter bar === */
.filter-bar { display: flex; gap: 6px; margin: 12px 0 18px; flex-wrap: wrap;
              align-items: center; }
.filter-bar button { padding: 5px 14px; border: 1px solid #ddd; border-radius: 14px;
                     background: #fff; cursor: pointer; font-size: 13px; color: #555;
                     font-family: inherit; }
.filter-bar button.active { background: #444; color: #fff; border-color: #444; }
.filter-bar button:hover { border-color: #444; }
.filter-bar input { padding: 5px 12px; border: 1px solid #ddd; border-radius: 14px;
                    font-size: 13px; min-width: 220px; font-family: inherit; }

/* === Sub-section in 估值/情绪 tab === */
.subsection { background: #fff; border: 1px solid #ececec; border-radius: 8px;
              padding: 16px 20px; margin: 14px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.03); }
.subsection > h3 { margin-top: 0; color: #2c3e50; font-size: 16px;
                   border-bottom: 1px dashed #e0e0e0; padding-bottom: 8px; }
.kv-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
           gap: 10px 18px; margin: 8px 0; }
.kv-grid .kv { font-size: 13px; }
.kv-grid .kv .k { color: #888; margin-right: 6px; }
.kv-grid .kv .v { font-weight: 600; color: #222; }

/* === Mini bar list (for 行业配置) === */
.minibar-list { list-style: none; padding: 0; margin: 6px 0; }
.minibar-list li { display: flex; align-items: center; gap: 10px; padding: 4px 0;
                   font-size: 13px; }
.minibar-list .name { width: 140px; color: #444; }
.minibar-list .bar { flex: 1; height: 6px; background: #f0f0f0; border-radius: 3px;
                     overflow: hidden; }
.minibar-list .bar > span { display: block; height: 100%; background: #3498db; }
.minibar-list .pct { width: 50px; text-align: right; color: #555; }

/* === Claude AI 投资指南 === */
.ai-pane { border-radius: 12px; padding: 6px 28px 28px;
           background: linear-gradient(180deg, #f5f9ff 0%, #fff 100%);
           border: 1px solid #d6e3f5; box-shadow: 0 2px 12px rgba(60, 100, 180, 0.06); }
.ai-pane .ai-badge { display: inline-block; padding: 5px 14px; margin: 14px 0 0;
                     background: #2c3e50; color: #fff; border-radius: 14px; font-size: 12px;
                     letter-spacing: 1px; }
.ai-pane .ai-meta { color: #888; font-size: 12px; margin: 6px 0 12px; }
.ai-pane h2 { border-left-color: #2c3e50; color: #2c3e50; margin-top: 14px; }
.ai-pane h3 { color: #2c3e50; margin-top: 22px; padding-bottom: 4px;
              border-bottom: 1px dashed #d6e3f5; }
.ai-pane h4 { color: #34495e; margin-top: 16px; }
.ai-pane ul, .ai-pane ol { margin: 8px 0; padding-left: 24px; }
.ai-pane li { margin: 4px 0; line-height: 1.7; }
.ai-pane p { line-height: 1.75; margin: 10px 0; }
.ai-pane strong { color: #c0392b; }
.ai-pane code { background: #f0f4fa; padding: 1px 6px; border-radius: 3px;
                font-size: 0.92em; color: #c0392b; }
.ai-pane blockquote { border-left: 3px solid #d6e3f5; padding: 4px 14px;
                      margin: 12px 0; color: #555; background: #f8fafd; }

/* === Sub-nav inside AI pane (auto from H2) === */
.ai-subnav { position: sticky; top: 64px; background: rgba(245, 249, 255, 0.95);
             backdrop-filter: blur(8px); padding: 10px 0; margin: 8px -28px 16px;
             padding-left: 28px; padding-right: 28px; border-bottom: 1px dashed #d6e3f5;
             display: flex; flex-wrap: wrap; gap: 6px; z-index: 10; }
.ai-subnav a { padding: 4px 12px; border-radius: 12px; font-size: 12px;
               color: #2c3e50; text-decoration: none; border: 1px solid #d6e3f5;
               background: #fff; }
.ai-subnav a:hover { background: #2c3e50; color: #fff; }

.disclaimer { color: #888; font-size: 12px; margin-top: 40px;
              border-top: 1px solid #eee; padding-top: 14px; }
</style>
</head>
<body>

<header class="report-head">
  <h1>A 股基金每日分析报告</h1>
  <div class="report-meta">生成时间：{{ date }} &nbsp;·&nbsp; 共 {{ fund_summaries|length }} 只基金</div>
</header>

{% if health_failed or health_empty %}
<div class="health-banner">
  <span class="label">⚠ 数据采集状态：</span>
  {% for k in health_failed %}<span class="tag tag-failed">{{ k }} 失败</span>{% endfor %}
  {% for k in health_empty %}<span class="tag tag-empty">{{ k }} 空</span>{% endfor %}
</div>
{% endif %}

<nav class="tab-nav" id="tabNav">
  {% if llm_advice_html %}
  <button class="ai-tab active" data-tab="ai">🤖 Claude AI 投资指南</button>
  {% endif %}
  <button data-tab="market" {% if not llm_advice_html %}class="active"{% endif %}>一、大盘行情</button>
  <button data-tab="valsent">二、估值 & 情绪</button>
  <button data-tab="sector">三、板块资金</button>
  <button data-tab="news">四、消息政策</button>
  <button data-tab="funds">五、持仓概览</button>
  <button data-tab="holdings">六、持仓穿透</button>
  <button data-tab="decisions">七、买卖建议</button>
</nav>

{% if llm_advice_html %}
<section id="tab-ai" class="tab-pane ai-pane active">
  <span class="ai-badge">🤖 Claude {{ llm_model or 'AI' }} 投资指南</span>
  <div class="ai-meta">基于当日量化数据 + 30 日大盘走势 + 7 日政策面综合生成</div>
  <div id="aiSubnav" class="ai-subnav"></div>
  <div id="aiContent">{{ llm_advice_html | safe }}</div>
</section>
{% endif %}

<section id="tab-market" class="tab-pane {% if not llm_advice_html %}active{% endif %}">
  <h2>一、大盘行情</h2>
  <table>
    <tr><th>指数</th><th>点位</th><th>涨跌幅</th><th>成交额(亿)</th></tr>
    {% for name, info in market_data.items() %}
    <tr>
      <td>{{ name }}</td>
      <td>{{ "%.2f"|format(info.price) if info.price is number else "-" }}</td>
      <td class="{{ 'up' if info.change_pct and info.change_pct > 0 else 'down' if info.change_pct and info.change_pct < 0 else 'flat' }}">
        {{ "%+.2f%%"|format(info.change_pct) if info.change_pct is number else "-" }}
      </td>
      <td>{{ "%.0f"|format(info.amount/1e8) if info.amount is number else "-" }}</td>
    </tr>
    {% endfor %}
  </table>
  <p>
    <strong>大盘趋势：</strong>{{ market_tech.trend }}（{{ market_tech.ma_signal }}）&nbsp;|&nbsp;
    <strong>MACD：</strong>{{ market_tech.macd_signal }}&nbsp;|&nbsp;
    <strong>RSI：</strong>{{ "%.1f"|format(market_tech.rsi_value) }}（{{ market_tech.rsi_signal }}）&nbsp;|&nbsp;
    <strong>北向资金：</strong>
    <span class="{{ 'up' if north_money > 0 else 'down' }}">{{ "%+.1f 亿"|format(north_money) }}</span>
  </p>
</section>

<section id="tab-valsent" class="tab-pane">
  <h2>二、估值 & 情绪</h2>

  {% if valuations %}
  <div class="subsection">
    <h3>2.1 指数估值分位（PE/PB · 5Y / 10Y）</h3>
    <p style="color:#777; font-size:13px;">
      绿色 &lt; 30% 偏便宜 · 黄色 30-70% 中性 · 红色 &gt; 70% 偏贵
    </p>
    <table>
      <tr><th>指数</th><th>PE</th><th>PE 5Y 分位</th><th>PE 10Y 分位</th><th>PB</th><th>PB 10Y 分位</th></tr>
      {% for name, v in valuations.items() %}
      <tr>
        <td><strong>{{ name }}</strong></td>
        <td>{{ "%.2f"|format(v.pe) if v.pe is number else "-" }}</td>
        <td>{{ qbar(v.pe_pct_5y) | safe }}</td>
        <td>{{ qbar(v.pe_pct_10y) | safe }}</td>
        <td>{{ "%.2f"|format(v.pb) if v.pb is number else "-" }}</td>
        <td>{{ qbar(v.pb_pct_10y) | safe }}</td>
      </tr>
      {% endfor %}
    </table>
  </div>
  {% endif %}

  {% if breadth and (breadth.zt_count is not none or breadth.dt_count is not none) %}
  <div class="subsection">
    <h3>2.2 市场宽度（赚钱效应）</h3>
    <div class="kv-grid">
      {% if breadth.zt_count is not none %}<div class="kv"><span class="k">涨停</span><span class="v up">{{ breadth.zt_count }} 家</span></div>{% endif %}
      {% if breadth.dt_count is not none %}<div class="kv"><span class="k">跌停</span><span class="v down">{{ breadth.dt_count }} 家</span></div>{% endif %}
      {% if breadth.strong_count is not none %}<div class="kv"><span class="k">强势股</span><span class="v">{{ breadth.strong_count }} 家</span></div>{% endif %}
      {% if breadth.max_consecutive is not none %}<div class="kv"><span class="k">最高连板</span><span class="v">{{ breadth.max_consecutive }} 板</span></div>{% endif %}
    </div>
    {% if breadth.consecutive_top %}
    <p style="color:#666; font-size:13px;">
      <strong>高度板代表：</strong>
      {% for name, board in breadth.consecutive_top %}{{ name }}({{ board }}板){% if not loop.last %}、{% endif %}{% endfor %}
    </p>
    {% endif %}
  </div>
  {% endif %}

  {% if margin_summary %}
  <div class="subsection">
    <h3>2.3 沪市两融余额（亿元 · 近 20 个交易日）</h3>
    <div class="kv-grid">
      <div class="kv"><span class="k">最新余额</span><span class="v">{{ "%.0f"|format(margin_summary.latest) }} 亿</span></div>
      <div class="kv"><span class="k">区间起点</span><span class="v">{{ "%.0f"|format(margin_summary.first) }} 亿</span></div>
      <div class="kv"><span class="k">变动</span>
        <span class="v {{ 'up' if margin_summary.change_pct > 0 else 'down' }}">
          {{ "%+.2f%%"|format(margin_summary.change_pct) }}
        </span>
      </div>
      {% if margin_summary.financing %}
      <div class="kv"><span class="k">融资余额</span><span class="v">{{ "%.0f"|format(margin_summary.financing) }} 亿</span></div>
      {% endif %}
      {% if margin_summary.short_balance %}
      <div class="kv"><span class="k">融券余额</span><span class="v">{{ "%.0f"|format(margin_summary.short_balance) }} 亿</span></div>
      {% endif %}
    </div>
  </div>
  {% endif %}

  {% if overseas %}
  <div class="subsection">
    <h3>2.4 海外市场</h3>
    <table>
      <tr><th>指数</th><th>点位</th><th>涨跌幅</th><th>数据日</th></tr>
      {% for name, v in overseas.items() %}
      {% if v.price is number %}
      <tr>
        <td><strong>{{ name }}</strong></td>
        <td>{{ "%.2f"|format(v.price) }}</td>
        <td class="{{ 'up' if v.change_pct and v.change_pct > 0 else 'down' if v.change_pct and v.change_pct < 0 else 'flat' }}">
          {{ "%+.2f%%"|format(v.change_pct) if v.change_pct is number else "-" }}
        </td>
        <td class="flat">{{ v.date or "-" }}</td>
      </tr>
      {% endif %}
      {% endfor %}
    </table>
  </div>
  {% endif %}

  {% if etf_premium %}
  <div class="subsection">
    <h3>2.5 场内 ETF 折溢价</h3>
    <p style="color:#777; font-size:13px;">
      溢价率 &gt; +1.5% 通常意味场内追涨,有回归风险; &lt; -0.5% 折价时场内便宜
    </p>
    <table>
      <tr><th>代码</th><th>名称</th><th>现价</th><th>当日涨跌</th><th>溢价率</th></tr>
      {% for code, v in etf_premium.items() %}
      {% if v %}
      <tr>
        <td><strong>{{ code }}</strong></td>
        <td>{{ v.name or '-' }}</td>
        <td>{{ "%.3f"|format(v.price) if v.price is number else "-" }}</td>
        <td class="{{ 'up' if v.change_pct and v.change_pct > 0 else 'down' if v.change_pct and v.change_pct < 0 else 'flat' }}">
          {{ "%+.2f%%"|format(v.change_pct) if v.change_pct is number else "-" }}
        </td>
        <td class="{{ 'prem-high' if v.premium_pct and v.premium_pct >= 1.5 else 'prem-low' if v.premium_pct and v.premium_pct <= -0.5 else 'prem-mid' }}">
          {% if v.premium_pct is number %}{{ "%+.2f%%"|format(v.premium_pct) }}{% else %}-{% endif %}
        </td>
      </tr>
      {% endif %}
      {% endfor %}
    </table>
  </div>
  {% endif %}
</section>

<section id="tab-sector" class="tab-pane">
  <h2>三、板块资金流向 TOP10</h2>
  {% if sector_data %}
  <table>
    <tr><th>板块</th><th>涨跌幅</th><th>主力净流入(亿)</th></tr>
    {% for s in sector_data[:10] %}
    <tr>
      <td>{{ s.sector }}</td>
      <td class="{{ 'up' if s.change_pct and s.change_pct > 0 else 'down' if s.change_pct and s.change_pct < 0 else 'flat' }}">
        {{ "%+.2f%%"|format(s.change_pct) if s.change_pct is number else "-" }}
      </td>
      <td class="{{ 'up' if s.main_net_flow and s.main_net_flow > 0 else 'down' }}">
        {{ "%+.2f"|format(s.main_net_flow/1e8) if s.main_net_flow is number else "-" }}
      </td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p class="flat">暂无板块资金数据</p>
  {% endif %}
</section>

<section id="tab-news" class="tab-pane">
  <h2>四、消息面 & 政策面</h2>
  <p><strong>消息面情绪评分：</strong>
    <span class="{{ 'score-pos' if sentiment.score > 15 else 'score-neg' if sentiment.score < -15 else 'score-mid' }}">
      {{ "%+.1f"|format(sentiment.score) }}
    </span> &nbsp;&nbsp; {{ sentiment.summary }}</p>
  <p><strong>政策面评分：</strong>
    <span class="{{ 'score-pos' if policy.score > 15 else 'score-neg' if policy.score < -15 else 'score-mid' }}">
      {{ "%+.1f"|format(policy.score) }}
    </span> &nbsp;&nbsp; {{ policy.direction }}：{{ policy.summary }}</p>

  {% if policy.hits %}
  <h3>政策关键命中</h3>
  <ul>
    {% for h in policy.hits[:8] %}
    <li><span class="{{ 'score-pos' if h.score > 0 else 'score-neg' }}">[{{ h.keywords }}]</span> {{ h.title }}</li>
    {% endfor %}
  </ul>
  {% endif %}

  {% if news_top %}
  <h3>今日要闻 TOP5</h3>
  <ul>
    {% for n in news_top[:5] %}
    <li>{{ n.title }} <span class="flat">{{ n.time }}</span></li>
    {% endfor %}
  </ul>
  {% endif %}
</section>

<section id="tab-funds" class="tab-pane">
  <h2>五、持仓基金概览</h2>
  <table>
    <tr><th>代码</th><th>名称</th><th>主题</th><th>最新净值</th><th>盘中估算</th><th>估算涨跌</th><th>近20日</th><th>波动率</th></tr>
    {% for s in fund_summaries %}
    <tr>
      <td>{{ s.code }}</td>
      <td>{{ s.name }}</td>
      <td>{{ s.theme or '-' }}</td>
      <td>{{ "%.4f"|format(s.last_nav) if s.last_nav is number else "-" }}</td>
      <td>{{ "%.4f"|format(s.estimate_nav) if s.estimate_nav is number else "-" }}</td>
      <td class="{{ 'up' if s.estimate_pct and s.estimate_pct > 0 else 'down' if s.estimate_pct and s.estimate_pct < 0 else 'flat' }}">
        {{ "%+.2f%%"|format(s.estimate_pct) if s.estimate_pct is number else "-" }}
      </td>
      <td class="{{ 'up' if s.change_20d and s.change_20d > 0 else 'down' if s.change_20d and s.change_20d < 0 else 'flat' }}">
        {{ "%+.2f%%"|format(s.change_20d) if s.change_20d is number else "-" }}
      </td>
      <td>{{ "%.1f%%"|format(s.volatility) if s.volatility is number else "-" }}</td>
    </tr>
    {% endfor %}
  </table>
</section>

<section id="tab-holdings" class="tab-pane">
  <h2>六、持仓穿透（最新季报口径）</h2>
  <p style="color:#888; font-size:13px;">基金前 10 大重仓股 + 行业配置 + 重仓股当日涨跌归因。季报披露口径,可能滞后 1-3 个月。</p>
  <div class="filter-bar" id="holdingsFilter">
    <input type="text" id="holdingsSearch" placeholder="搜索基金代码 / 名称 / 主题 / 重仓股..." />
  </div>
  {% for fh in fund_holdings_table %}
  <details class="fund-card" data-search="{{ fh.search }}">
    <summary>
      <span class="card-title">
        {{ fh.code }} · {{ fh.name }}
        <span style="color:#aaa; font-weight:normal; font-size:12px;">{{ fh.theme }}</span>
      </span>
      <span class="card-meta">
        {% if fh.contrib_total is not none %}
        当日重仓股贡献加总:
        <span class="{{ 'score-pos' if fh.contrib_total > 0 else 'score-neg' if fh.contrib_total < 0 else 'score-mid' }}">
          {{ "%+.2f%%"|format(fh.contrib_total) }}
        </span>
        {% endif %}
        {% if fh.quarter %}<span style="color:#bbb;">· {{ fh.quarter }}</span>{% endif %}
      </span>
    </summary>
    <div class="card-body">
      {% if fh.industries %}
      <h4 style="margin:12px 0 6px; color:#444;">行业配置</h4>
      <ul class="minibar-list">
        {% for ind in fh.industries %}
        <li>
          <span class="name">{{ ind.industry }}</span>
          <span class="bar"><span style="width:{{ ind.weight_capped }}%"></span></span>
          <span class="pct">{{ "%.1f"|format(ind.weight) }}%</span>
        </li>
        {% endfor %}
      </ul>
      {% endif %}

      {% if fh.rows %}
      <h4 style="margin:14px 0 6px; color:#444;">前 10 大重仓股 / 当日归因</h4>
      <table>
        <tr><th>股票</th><th style="text-align:right;">权重</th><th style="text-align:right;">当日涨跌</th><th style="text-align:right;">贡献(权重×涨跌)</th></tr>
        {% for r in fh.rows %}
        <tr>
          <td><strong>{{ r.name }}</strong> <span style="color:#aaa; font-size:12px;">{{ r.code }}</span></td>
          <td style="text-align:right;">{{ "%.2f%%"|format(r.weight) if r.weight is number else "-" }}</td>
          <td style="text-align:right;" class="{{ 'up' if r.change_pct and r.change_pct > 0 else 'down' if r.change_pct and r.change_pct < 0 else 'flat' }}">
            {{ "%+.2f%%"|format(r.change_pct) if r.change_pct is number else "-" }}
          </td>
          <td style="text-align:right;" class="{{ 'up' if r.contrib and r.contrib > 0 else 'down' if r.contrib and r.contrib < 0 else 'flat' }}">
            {{ "%+.3f"|format(r.contrib) if r.contrib is number else "-" }}
          </td>
        </tr>
        {% endfor %}
      </table>
      {% if fh.leaders or fh.laggards %}
      <p style="font-size:13px; color:#555;">
        {% if fh.leaders %}<strong>领涨重仓:</strong> {% for l in fh.leaders %}{{ l.name }}({{ "%+.2f%%"|format(l.change_pct) }}){% if not loop.last %}、{% endif %}{% endfor %}{% endif %}
        {% if fh.laggards %}<br><strong>领跌重仓:</strong> {% for l in fh.laggards %}{{ l.name }}({{ "%+.2f%%"|format(l.change_pct) }}){% if not loop.last %}、{% endif %}{% endfor %}{% endif %}
      </p>
      {% endif %}
      {% else %}
      <p class="flat" style="margin:12px 0 4px;">暂无持仓穿透数据(季报可能尚未披露)</p>
      {% endif %}
    </div>
  </details>
  {% endfor %}
</section>

<section id="tab-decisions" class="tab-pane">
  <h2>七、买卖建议（量化模型，{{ decisions|length }} 只）</h2>
  <div class="filter-bar" id="actionFilter">
    <button data-action="all" class="active">全部</button>
    <button data-action="买入">买入</button>
    <button data-action="加仓">加仓</button>
    <button data-action="持有">持有</button>
    <button data-action="减仓">减仓</button>
    <button data-action="卖出">卖出</button>
  </div>
  {% for d in decisions %}
  <details class="fund-card" data-action="{{ d.action }}">
    <summary>
      <span class="card-title">
        <span class="action action-{{ action_class[d.action] }}">{{ d.action }}</span>
        {{ d.fund_code }} · {{ d.fund_name }}
      </span>
      <span class="card-meta">
        评分
        <span class="{{ 'score-pos' if d.score > 15 else 'score-neg' if d.score < -15 else 'score-mid' }}">
          {{ "%+.1f"|format(d.score) }}
        </span>
        · 置信度 {{ d.confidence }}
      </span>
    </summary>
    <div class="card-body">
      <p><strong>决策依据：</strong></p>
      <ul class="reasons">
      {% for r in d.reasons %}<li>{{ r }}</li>{% endfor %}
      </ul>
      {% if d.warnings %}
      <p class="warning"><strong>风险提示：</strong></p>
      <ul class="reasons">
      {% for w in d.warnings %}<li class="warning">{{ w }}</li>{% endfor %}
      </ul>
      {% endif %}
      <p><strong>评分明细：</strong>
        {% for k, v in d.breakdown.items() %}{{ k }} {{ "%+.1f"|format(v) }} &nbsp;{% endfor %}
      </p>
    </div>
  </details>
  {% endfor %}
</section>

<p class="disclaimer">
本报告由量化模型自动生成，所有结论仅供参考，不构成投资建议。
投资有风险，决策需谨慎。请结合自身风险承受能力独立判断。
</p>

<script>
(function() {
  // === Tab 切换 ===
  const tabs = document.querySelectorAll('#tabNav button');
  const panes = document.querySelectorAll('.tab-pane');
  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.tab;
      tabs.forEach(b => b.classList.toggle('active', b === btn));
      panes.forEach(p => p.classList.toggle('active', p.id === 'tab-' + id));
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });

  // === 七、买卖建议: 按操作类型筛选 ===
  const filter = document.getElementById('actionFilter');
  if (filter) {
    filter.addEventListener('click', e => {
      if (e.target.tagName !== 'BUTTON') return;
      const action = e.target.dataset.action;
      filter.querySelectorAll('button').forEach(b => b.classList.toggle('active', b === e.target));
      document.querySelectorAll('#tab-decisions .fund-card').forEach(card => {
        card.style.display = (action === 'all' || card.dataset.action === action) ? '' : 'none';
      });
    });
  }

  // === 六、持仓穿透: 关键字搜索 ===
  const search = document.getElementById('holdingsSearch');
  if (search) {
    search.addEventListener('input', e => {
      const q = e.target.value.trim().toLowerCase();
      document.querySelectorAll('#tab-holdings .fund-card').forEach(card => {
        const text = (card.dataset.search || '').toLowerCase();
        card.style.display = (!q || text.includes(q)) ? '' : 'none';
      });
    });
  }

  // === AI 指南: 自动从 H2 生成跳转锚点 ===
  const aiContent = document.getElementById('aiContent');
  const aiSubnav = document.getElementById('aiSubnav');
  if (aiContent && aiSubnav) {
    const heads = aiContent.querySelectorAll('h2');
    if (heads.length > 1) {
      heads.forEach((h, i) => {
        const id = 'ai-h-' + i;
        h.id = id;
        const a = document.createElement('a');
        a.href = '#' + id;
        a.textContent = h.textContent.trim();
        a.addEventListener('click', e => {
          e.preventDefault();
          h.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
        aiSubnav.appendChild(a);
      });
    } else {
      aiSubnav.style.display = 'none';
    }
  }
})();
</script>

</body>
</html>"""


ACTION_CLASS = {
    "买入": "buy",
    "加仓": "add",
    "持有": "hold",
    "减仓": "reduce",
    "卖出": "sell",
}


def _qbar(p) -> str:
    """估值分位 → 内联 SVG-ish HTML 条 (用 div 实现)"""
    if not isinstance(p, (int, float)):
        return '<span class="qbar-text">-</span>'
    cls = "q-low" if p < 30 else "q-mid" if p < 70 else "q-high"
    return (
        f'<span class="qbar"><span class="marker" style="left: {min(99, max(0, p)):.0f}%"></span></span>'
        f'<span class="qbar-text {cls}">{p:.0f}%</span>'
    )


def _markdown_to_html(text: str) -> str:
    """把 Claude 返回的 Markdown 文本转 HTML 片段; 转换失败时回落为 <pre>"""
    if not text:
        return ""
    try:
        import markdown as md
        return md.markdown(
            text,
            extensions=["extra", "tables", "sane_lists"],
            output_format="html5",
        )
    except Exception:
        import html as _html
        return f"<pre>{_html.escape(text)}</pre>"


def _build_holdings_table(
    fund_summaries: List[Dict],
    fund_holdings: Optional[Dict[str, Dict]],
) -> List[Dict]:
    """把 fund_holdings 整理成模板可直接遍历的列表."""
    if not fund_summaries:
        return []
    out: List[Dict] = []
    for s in fund_summaries:
        code = s.get("code")
        name = s.get("name", "")
        theme = s.get("theme", "")
        h = (fund_holdings or {}).get(code) or {}

        top = h.get("top_holdings")
        ind = h.get("industries")
        attribution = h.get("attribution") or []

        attr_map = {a.get("stock_code"): a for a in attribution if a.get("stock_code")}

        rows: List[Dict] = []
        contrib_total = None
        quarter = ""
        if isinstance(top, pd.DataFrame) and not top.empty:
            contrib_total = 0.0
            had = False
            for _, r in top.iterrows():
                sc = str(r.get("stock_code", "")).zfill(6)
                attr = attr_map.get(sc, {})
                contrib = attr.get("contribution")
                if isinstance(contrib, (int, float)):
                    contrib_total += contrib
                    had = True
                rows.append({
                    "code": sc,
                    "name": str(r.get("stock_name", "")),
                    "weight": r.get("weight"),
                    "change_pct": attr.get("change_pct"),
                    "contrib": contrib,
                })
            if not had:
                contrib_total = None
            if "quarter" in top.columns and not top["quarter"].empty:
                quarter = str(top["quarter"].iloc[0])

        industries: List[Dict] = []
        if isinstance(ind, pd.DataFrame) and not ind.empty:
            max_w = float(ind["weight"].max()) if "weight" in ind.columns and not ind["weight"].empty else 1.0
            for _, r in ind.head(6).iterrows():
                w = r.get("weight")
                if not isinstance(w, (int, float)):
                    continue
                industries.append({
                    "industry": str(r.get("industry", "")),
                    "weight": float(w),
                    "weight_capped": min(100, (float(w) / max_w * 100)) if max_w else 0,
                })

        # 领涨/领跌
        attr_with_chg = [a for a in attribution if isinstance(a.get("change_pct"), (int, float))]
        attr_with_chg.sort(key=lambda x: x["change_pct"], reverse=True)
        leaders = [
            {"name": a.get("stock_name", ""), "change_pct": a["change_pct"]}
            for a in attr_with_chg[:3] if a.get("stock_name")
        ]
        laggards = []
        if len(attr_with_chg) > 3:
            laggards = [
                {"name": a.get("stock_name", ""), "change_pct": a["change_pct"]}
                for a in attr_with_chg[-3:] if a.get("stock_name")
            ]

        # 搜索字符串(给前端 input 过滤)
        search_bits = [str(code), name, theme]
        for r in rows:
            search_bits.append(r["name"])
            search_bits.append(r["code"])
        out.append({
            "code": code,
            "name": name,
            "theme": theme,
            "rows": rows,
            "industries": industries,
            "contrib_total": contrib_total,
            "quarter": quarter,
            "leaders": leaders,
            "laggards": laggards,
            "search": " ".join(search_bits),
        })
    return out


def _margin_summary(margin_history: Optional[pd.DataFrame]) -> Optional[Dict]:
    if margin_history is None or margin_history.empty or "total_balance" not in margin_history.columns:
        return None
    s = margin_history["total_balance"].dropna()
    if len(s) < 2:
        return None
    first = float(s.iloc[0])
    last = float(s.iloc[-1])
    if first == 0:
        return None
    last_row = margin_history.iloc[-1]
    return {
        "first": first,
        "latest": last,
        "change_pct": (last - first) / first * 100,
        "financing": float(last_row.get("financing_balance")) if isinstance(last_row.get("financing_balance"), (int, float)) else None,
        "short_balance": float(last_row.get("short_balance")) if isinstance(last_row.get("short_balance"), (int, float)) else None,
    }


def render_html_report(
    *,
    output_dir: str | Path = "reports",
    market_data: Dict,
    sector_data: List[Dict],
    fund_summaries: List[Dict],
    decisions: List[Decision],
    sentiment: SentimentResult,
    policy: PolicySignal,
    north_money: float,
    market_tech: TechnicalSignal,
    news_top: List[Dict],
    llm_advice: Optional[str] = None,
    llm_model: Optional[str] = None,
    valuations: Optional[Dict[str, Dict]] = None,
    margin_history: Optional[pd.DataFrame] = None,
    breadth: Optional[Dict] = None,
    overseas: Optional[Dict[str, Dict]] = None,
    etf_premium: Optional[Dict[str, Dict]] = None,
    fund_holdings: Optional[Dict[str, Dict]] = None,
    data_health: Optional[Dict[str, str]] = None,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    health_failed = [k for k, v in (data_health or {}).items() if v == "failed"]
    health_empty = [k for k, v in (data_health or {}).items() if v == "empty"]

    template = Template(HTML_TEMPLATE)
    template.globals["qbar"] = _qbar

    html = template.render(
        date=datetime.now().strftime("%Y-%m-%d %H:%M"),
        market_data=market_data,
        sector_data=sector_data,
        fund_summaries=fund_summaries,
        decisions=decisions,
        sentiment=sentiment,
        policy=policy,
        north_money=north_money,
        market_tech=market_tech,
        news_top=news_top,
        action_class=ACTION_CLASS,
        llm_advice_html=_markdown_to_html(llm_advice) if llm_advice else "",
        llm_model=llm_model,
        valuations=valuations or {},
        margin_summary=_margin_summary(margin_history),
        breadth=breadth or {},
        overseas=overseas or {},
        etf_premium=etf_premium or {},
        fund_holdings_table=_build_holdings_table(fund_summaries, fund_holdings),
        health_failed=health_failed,
        health_empty=health_empty,
    )

    today = datetime.now().strftime("%Y%m%d")
    out = output_dir / f"report_{today}.html"
    out.write_text(html, encoding="utf-8")
    return out
