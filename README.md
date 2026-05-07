# 基金买卖分析助手 (Fund Advisor)

A股基金买卖决策辅助工具，每日交易日结束前提供基于多维度数据的买卖建议。

## 功能特性

- **大盘数据分析**：上证、深证、创业板、沪深300等核心指数技术分析
- **基金数据追踪**：跟踪持仓基金的实时表现、估值、历史走势
- **消息面分析**：抓取财经新闻，进行情绪分析
- **政策面解读**：识别宏观政策、行业政策对市场的影响
- **资金面监控**：北向资金、主力资金流向
- **综合决策建议**：基于多维度评分给出买入/持有/卖出建议
- **每日报告**：交易日结束后生成详细的分析报告

## 技术栈

- Python 3.9+
- akshare（免费开源金融数据）
- pandas / numpy（数据处理）
- pandas-ta（技术指标）
- snownlp / jieba（中文文本分析）
- rich（命令行美化）
- jinja2（HTML 报告模板）

## 安装

```bash
# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

## 配置

在 `config/holdings.yaml` 中配置你的基金持仓：

```yaml
holdings:
  - code: "000001"
    name: "华夏成长混合"
    shares: 1000
    cost_price: 1.234
  - code: "110011"
    name: "易方达中小盘混合"
    shares: 500
    cost_price: 5.678
```

## 使用方法

```bash
# 生成今日分析报告
python -m fund_advisor analyze

# 查看持仓状态
python -m fund_advisor holdings

# 仅查看大盘分析
python -m fund_advisor market

# 生成 HTML 报告
python -m fund_advisor analyze --html
```

## 免责声明

本工具仅供学习研究使用，所有分析结果不构成投资建议。投资有风险，决策需谨慎。
