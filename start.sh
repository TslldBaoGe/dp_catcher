#!/usr/bin/env bash
# DrissionPage 元素捕获器 启动脚本（Git Bash / WSL）
# 用法：./start.sh [--headless] [--port 8777] [--no-browser]
set -euo pipefail

PY="/f/Miniconda39/envs/python31013/python.exe"
if [ ! -f "$PY" ]; then
  PY="$(command -v python3 || command -v python)"
  echo "[提示] 未找到 F:\\Miniconda39\\envs\\python31013，改用 $PY"
fi

cd "$(dirname "$0")"

if ! "$PY" -c "import DrissionPage, fastapi, uvicorn" >/dev/null 2>&1; then
  echo "[错误] 缺少依赖，请先执行："
  echo "    \"$PY\" -m pip install -r requirements.txt"
  exit 1
fi

echo "正在启动 DrissionPage 元素捕获器..."
exec "$PY" run.py "$@"
