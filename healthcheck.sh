#!/usr/bin/env bash
# Codex 雷达 MCP Server 健康检查
BASE="${1:-http://127.0.0.1:8765/mcp}"
echo "== 探测 $BASE =="
# MCP initialize (streamable http)
curl -sS -m8 -X POST "$BASE" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-03-26\",\"capabilities\":{},\"clientInfo\":{\"name\":\"healthcheck\",\"version\":\"1\"}}}" 2>&1 | grep -oE "serverInfo[^}]*}" | head -1
