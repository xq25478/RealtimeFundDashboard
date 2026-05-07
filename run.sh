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

# 从 config.json 读端口 (默认 31009)
PORT=$(python3 -c "
import json, sys
try:
    cfg = json.load(open('config/config.json'))
    print(cfg.get('server', {}).get('port', 31009))
except Exception:
    print(31009)
" 2>/dev/null || echo 31009)

echo "[*] 清理端口 $PORT 和旧的 fund_advisor 进程 ..."

# 1. 杀掉旧的 python -m fund_advisor 进程
OLD_PIDS=$(pgrep -f "python -m fund_advisor" 2>/dev/null || true)
if [ -n "$OLD_PIDS" ]; then
    echo "    发现残留进程: $OLD_PIDS"
    kill $OLD_PIDS 2>/dev/null || true
    sleep 0.5
    # 还活着就 -9
    STILL=$(pgrep -f "python -m fund_advisor" 2>/dev/null || true)
    if [ -n "$STILL" ]; then
        kill -9 $STILL 2>/dev/null || true
    fi
fi

# 2. 杀掉仍占用端口的任何进程 (兜底)
PORT_PIDS=$(lsof -ti:"$PORT" 2>/dev/null || true)
if [ -n "$PORT_PIDS" ]; then
    echo "    端口占用进程: $PORT_PIDS"
    kill $PORT_PIDS 2>/dev/null || true
    sleep 0.5
    STILL=$(lsof -ti:"$PORT" 2>/dev/null || true)
    if [ -n "$STILL" ]; then
        kill -9 $STILL 2>/dev/null || true
    fi
fi

# 可选参数: 自定义 holdings 配置
if [ -n "$1" ]; then
    export FUND_CONFIG="$1"
fi

echo "[*] 启动 dashboard (port=$PORT) ..."
conda run -n funds --no-capture-output python -m fund_advisor
