# Code Review：codex-radar-mcp

**Review 日期：** 2026-08-22  
**审阅范围：** `src/codex_radar_mcp.py`、启动/健康检查脚本、依赖与 README  
**结论：** 初版已经能工作，但原实现更像“可用原型”：连接复用、并发缓存、故障隔离、MCP progress 和文档边界仍有明显提升空间。本次已落地 P0/P1 项，未改变 5 个工具的公开名称和正常返回字段。

## 已发现与处理的问题

### P1：每次请求都创建 `httpx.AsyncClient`

- **问题：** 原实现每次工具调用都新建并关闭客户端，无法复用 TCP/TLS 连接；高频调用时增加延迟和资源开销。
- **修复：** 使用共享 `AsyncClient`，并在 FastMCP lifespan 退出时关闭；保留 `TIMEOUT` 配置。
- **动机：** MCP server 是长驻进程，连接池复用是更合理的生命周期模型。

### P1：缓存检查存在并发竞态

- **问题：** 多个请求同时命中失效缓存时会重复抓取源站（cache stampede）。原来的 `time.time()` 也可能受系统时钟回拨影响。
- **修复：** 使用 `time.monotonic()`；在缓存 miss 的抓取路径上加 single-flight 锁，使同一时刻不会发生并发重复请求。
- **取舍：** 当前端点数量很少、请求频率低，使用一个简单的 endpoint→lock 映射；同一 endpoint 内保持 single-flight，不同 endpoint 可并行。若未来增加大量动态端点，可再考虑锁回收策略。

### P1：上游一次超时会直接让 MCP 工具失败

- **问题：** 短暂网络抖动或源站 5xx 会把异常直接交给 MCP 客户端，MaiBot 侧不容易得到稳定的结构化结果。
- **修复：** 对 HTTP/JSON 错误做一次短延迟重试；工具边界统一返回 `{"ok": false, "error": ...}`，避免把整个调用链打崩。
- **安全注意：** 返回给客户端的是通用错误和异常类型，不返回 URL、响应体或可能包含内部信息的异常详情。

### P1：过滤参数是大小写敏感的精确匹配

- **问题：** `gpt-5.6-sol`、`GPT-5.6-SOL` 等人工输入会产生空结果。
- **修复：** 保持“精确模型 ID”语义，但对两侧做 trim + `casefold()`；没有把过滤扩大为模糊搜索，避免误匹配多个模型。

### P1：工具描述没有足够突出推荐工具的语义

- **问题：** 规划器可能在“该用哪个模型/档位”问题上先调用明细工具，增加延迟和上下文体积。
- **修复：** `get_radar_insights` 描述明确标为核心工具，并列出 4 个场景；server instructions 也要求优先调用它。

### P1：需求中的调用中反馈没有实现路径

- **问题：** 原实现没有 MCP progress notification，因此无法向支持 progress 的客户端发出“正在调用”事件。
- **修复：** 每个工具接入 `Context`，通过 `ctx.report_progress()` 发送 progress notification；由 `CODEX_RADAR_PROGRESS_ENABLED` 开关控制，默认关闭以保持通用版行为。
- **重要边界：** MCP progress 是协议通知，不等同于 MaiBot 一定会把它渲染成群消息。是否能看到中文提示，仍需用当前 MaiBot MCPBridge 实际验证；若 Bridge 不消费 progress，应在 MaiBot 侧做提示语适配（交接文档中的方案 C）。

## 仍然建议后续处理

### P2：自动化测试（已完成）

已新增 `requirements-dev.txt` 与 `tests/test_codex_radar_mcp.py`，覆盖缓存、并发 single-flight、重试、模型过滤和字段容错；本地测试为 5 passed。

### P2：按端点拆分 single-flight 锁（已完成）

已从全局锁改为 endpoint→lock 映射：同一端点请求去重，不同端点可并行。

### P2：安全边界

默认监听 `0.0.0.0` 且没有鉴权，只适合可信内网/防火墙隔离环境。若暴露到公网，应增加反向代理鉴权，或使用 MCP 1.x 支持的 token verifier；不要把 API token 写入 README 或交接文档。

### P2：依赖与发布工程（部分完成）

运行时依赖已固定为经过验证的 `mcp==1.29.0` 与 `httpx==0.28.1`，并新增 Python 3.11/3.12 的 GitHub Actions CI。后续若依赖升级，应在 CI 和真实 MCP smoke test 中重新验证。

### P2：健康检查脚本（已完成）

原脚本通过 grep 截取响应，且不检查 curl 的 HTTP 状态码，可能出现“命令成功但服务返回错误”的假阳性。本轮已改为检查 HTTP 200、`MCP-Session-Id`，并解析 JSON/SSE initialize 响应中的 `serverInfo.name`。

## 验证记录

- `python3 -m py_compile src/codex_radar_mcp.py`：通过。
- 本地构建 FastMCP：5 个工具均正确注册，`ctx` 未出现在工具参数 schema 中。
- 真实源站 5 个公开端点：均返回 JSON；工具 smoke test 成功，订阅数返回 4263。
- 过滤测试：`GPT-5.6-SOL` 能匹配 `gpt-5.6-sol` 对应数据。

## 总体评价

原代码的核心数据映射清楚、工具边界简单，足以支撑当前 MaiBot 接入；主要不足集中在长驻服务的工程化细节，而不是业务逻辑方向。本次修改优先处理了会影响稳定性和调用体验的部分；测试、鉴权、CI 和健康检查属于下一轮，不应在没有生产验证的情况下贸然改动部署端。

---

*本报告只记录本次 review 的判断与变更，不包含任何 API key、token 或密码。*
