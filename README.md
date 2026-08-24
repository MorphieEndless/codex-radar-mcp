# 📡 Codex Radar MCP

> 把 [codexradar.com](https://codexradar.com) 的实时模型智商数据，封装成标准 **MCP（Model Context Protocol）服务器**，让 MaiBot、Claude Desktop、Cursor 等 AI 助手能随时回答——**"现在哪个模型最聪明？该换哪个档？有没有降智？"**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11%2B-green.svg)

---

## ✨ 特性

- **零配置数据源**：直接抓取 codexradar.com 的公开 JSON 接口，无需登录、无需 API Key、不解析 HTML
- **5 个现成工具**：综合智能 IQ、软件工程 IQ、视觉空间 IQ、社区体感分、订阅人数
- **分场景档位推荐**：自动给出「日常开发 / 难题攻坚 / 后台自动化 / 跑龙虾」各自的模型 + 推理档位建议（即雷达站上的"建议换 max/xhigh/low"）
- **开箱即用**：`pip install` + 一行启动，Streamable HTTP 传输，主流 MCP 客户端通吃
- **自带缓存**：120s 本地缓存，避免高频查询打爆源站

---

## 🏗️ 架构

```
codexradar.com（公开 JSON 接口，约 5 分钟刷新）
        │ HTTPS
        ▼
codex-radar-mcp（FastMCP · Streamable HTTP · 0.0.0.0:8765/mcp）
        │ 120s 本地缓存 / 中文档位对照 / 数据清洗
        ▼
MCP 客户端（MaiBot · Claude Desktop · Cursor · 任意 MCP Client）
```

---

## 🚀 快速开始

### 1. 安装

```bash
git clone https://github.com/MorphieEndless/codex-radar-mcp.git
cd codex-radar-mcp
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

### 2. 启动

```bash
./run.sh
# 或直接：venv/bin/python -u src/codex_radar_mcp.py
```

默认监听 `0.0.0.0:8765`，MCP 端点：`http://127.0.0.1:8765/mcp`

### 3. 健康检查

```bash
./healthcheck.sh http://127.0.0.1:8765/mcp
# 期望输出：✅ healthy: codex-radar 1.29.0
```

### 4. 接入 MCP 客户端

以 **Claude Desktop** 为例（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "codex-radar": {
      "url": "http://127.0.0.1:8765/mcp",
      "transport": "streamable_http"
    }
  }
}
```

### 5. 试试看

连上后直接问：

- "现在哪个模型综合 IQ 最高？"
- "日常开发用哪个模型的哪个档位最划算？"
- "gpt-5.6 最近有没有降智？"

---

## 🔧 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CODEX_RADAR_HOST` | `0.0.0.0` | 监听地址（容器/远程访问需保持非 127.0.0.1） |
| `CODEX_RADAR_PORT` | `8765` | 监听端口 |
| `CODEX_RADAR_TIMEOUT` | `15` | 抓取远端超时（秒） |
| `CODEX_RADAR_CACHE_TTL` | `120` | 本地缓存时间（秒） |
| `CODEX_RADAR_PROGRESS_ENABLED` | `false` | 是否发送 MCP progress notification；默认关闭，且不保证 MaiBot 将其渲染为群消息 |

---

## 🛠️ 工具清单

| 工具 | 参数 | 说明 |
|---|---|---|
| `get_model_ratings` | 无 | 近 24h 社区体感评分（按模型分组） |
| `get_radar_insights` | 无 | **★核心**：综合 IQ + 4 场景档位推荐 |
| `get_intelligence_effort` | `model?` | 软件工程 IQ 明细（含成本指数/agent 步数/24-48h 运行量，可过滤如 `gpt-5.6-sol` / `k3`） |
| `get_visual_spatial` | `model?` | 视觉空间 IQ（庞贝邻接 F1，含成本指数/agent 步数/运行量，可过滤） |
| `get_subscriber_count` | 无 | 雷达订阅人数 |

---

## 📊 示例输出（真实数据）

调用 `get_radar_insights` 返回（节选）：

```json
{
  "ok": true,
  "generated_at": "2026-08-22T11:56:08+00:00",
  "recommendations": [
    {
      "scene": "daily_development",
      "scene_cn": "日常开发",
      "items": [
        {"model": "gpt-5.6-sol", "effort": "high", "iq": 94.54, "average_cost_usd": 4.167},
        {"model": "gpt-5.5", "effort": "xhigh", "iq": 93.08, "average_cost_usd": 5.290}
      ]
    },
    {
      "scene": "hard_problems",
      "scene_cn": "难题攻坚",
      "items": [
        {"model": "gpt-5.6-sol", "effort": "ultra", "iq": 105.1, "average_cost_usd": 22.52}
      ]
    }
  ]
}
```

---

## 🤖 接入 MaiBot（麦麦）

本项目最初就是为 [MaiBot](https://github.com/RiverTwilight/YumeAgents) 定制的。MaiBot 通过 **MCPBridge 插件**接入：

1. 编辑 MCPBridge 插件的 `config.toml`，在 `[servers]` 段的 `claude_config_json` 中填入：

```json
{
  "mcpServers": {
    "codex-radar": {
      "url": "http://172.17.0.1:8765/mcp",
      "transport": "streamable_http",
      "enabled": true
    }
  }
}
```

> `172.17.0.1` 是 Docker 容器访问宿主机的 docker0 网关地址，按你的实际网络调整。

2. 在群里对麦麦发 `/mcp reconnect`（或重启 MaiBot 容器）
3. 发 `/mcp tools` 确认 5 个工具已列出

### 6. 测试

安装开发依赖并运行单元测试：

```bash
python3 -m venv venv
venv/bin/pip install -r requirements-dev.txt
venv/bin/pytest -q
```

测试覆盖模型过滤、缓存命中、并发 single-flight、上游失败重试和字段缺失时的输出。

---

## ⚠️ 免责声明

- 本项目的全部数据来自 [codexradar.com](https://codexradar.com) 的公开接口，**数据版权归源站所有**，本项目仅做转发与格式整理。
- 本项目与 codexradar.com 官方无任何隶属/合作关系。
- 请合理设置 `CODEX_RADAR_CACHE_TTL`，不要高频请求源站。
- 数据仅供参考，不构成任何决策依据。

---

## 📄 License

[MIT](LICENSE) © 2026 MorphieEndless
