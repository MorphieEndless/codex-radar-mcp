# Codex 雷达 MCP Streamable HTTP 健康检查
set -euo pipefail

BASE="${1:-http://127.0.0.1:8765/mcp}"
TIMEOUT="${HEALTHCHECK_TIMEOUT:-8}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

BODY="$TMP_DIR/body"
HEADERS="$TMP_DIR/headers"
PAYLOAD='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"codex-radar-healthcheck","version":"1"}}}'

echo "== 探测 $BASE =="
STATUS="$(curl -sS -m "$TIMEOUT" -o "$BODY" -D "$HEADERS" -w '%{http_code}' -X POST "$BASE" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data "$PAYLOAD")"

if [[ "$STATUS" != "200" ]]; then
  echo "❌ HTTP $STATUS" >&2
  cat "$BODY" >&2
  exit 1
fi

if ! grep -qi '^mcp-session-id:' "$HEADERS"; then
  echo "❌ HTTP 200，但响应缺少 MCP-Session-Id" >&2
  cat "$BODY" >&2
  exit 1
fi

# Streamable HTTP 可能返回 JSON 或 SSE；两者都检查 initialize.result.serverInfo。
python3 - "$BODY" <<'PY'
import json
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
candidates = [text]
candidates.extend(line[5:].strip() for line in text.splitlines() if line.startswith("data:"))
for candidate in candidates:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        continue
    server_info = payload.get("result", {}).get("serverInfo")
    if isinstance(server_info, dict) and server_info.get("name") == "codex-radar":
        print(f"✅ healthy: {server_info.get('name')} {server_info.get('version', '')}".rstrip())
        raise SystemExit(0)
print("❌ initialize 响应缺少 codex-radar serverInfo", file=sys.stderr)
print(text, file=sys.stderr)
raise SystemExit(1)
PY
