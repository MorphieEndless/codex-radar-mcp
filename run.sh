#!/usr/bin/env bash
# Codex 雷达 MCP Server 启动脚本（Streamable HTTP）
# 默认端点: http://0.0.0.0:8765/mcp
# 优先使用 venv 中的 Python，其次系统 Python3
set -e
cd "$(dirname "$0")"

if [ -x "venv/bin/python" ]; then
    PY="venv/bin/python"
else
    PY="python3"
fi

echo "[codex-radar-mcp] 使用解释器: $PY"
exec "$PY" -u src/codex_radar_mcp.py
