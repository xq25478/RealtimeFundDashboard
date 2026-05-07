#!/bin/bash
# 一键初始化脚本

set -e

cd "$(dirname "$0")"

echo "=== 基金分析助手 安装 ==="

PYTHON=${PYTHON:-python3}

echo ">> 创建虚拟环境..."
$PYTHON -m venv venv

echo ">> 激活虚拟环境..."
source venv/bin/activate

echo ">> 升级 pip..."
pip install -U pip wheel

echo ">> 安装依赖..."
pip install -r requirements.txt

echo ""
echo "=== 安装完成 ==="
echo ""
echo "使用方法："
echo "  source venv/bin/activate"
echo "  PYTHONPATH=src python -m fund_advisor analyze         # 完整分析"
echo "  PYTHONPATH=src python -m fund_advisor holdings        # 查看持仓"
echo "  PYTHONPATH=src python -m fund_advisor market          # 大盘速览"
echo "  PYTHONPATH=src python -m fund_advisor news --limit 20 # 财经快讯"
echo ""
echo "  # 输出 HTML 报告"
echo "  PYTHONPATH=src python -m fund_advisor analyze --html"
