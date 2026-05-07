#!/usr/bin/env bash
# 启动实时基金看板 - 默认 http://127.0.0.1:31009
# 凭据 / host / port 全部来自 config/config.json
# 首次使用: cp config/config.example.json config/config.json 并填入 api_key

set -e && clear
cd "$(dirname "$0")"

if [ ! -f config/config.json ]; then
    echo "[!] 缺少 config/config.json"
    echo "    请执行: cp config/config.example.json config/config.json"
    echo "    然后填入 anthropic.api_key"
    exit 1
fi

export PYTHONPATH=src

# 可选参数: 自定义 holdings 配置
if [ -n "$1" ]; then
    export FUND_CONFIG="$1"
fi

conda run -n funds --no-capture-output python -m fund_advisor
