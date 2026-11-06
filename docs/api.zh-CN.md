# API 参考

后端只提供本机 HTTP API。以下路径统一带 `/api/v1` 前缀。桌面端从 Electron 主进程获取基地址；
端口在启动时分配。

- **基地址（安装版）**：`http://127.0.0.1:<port>/api/v1`
- **认证**：桌面模式下每个请求都需要 `X-Slothy-Token: <本次启动令牌>`。令牌由 Electron
  主进程生成，渲染进程不可见。
- **内容类型**：除附件原始二进制外均为 JSON。
- **错误**：`{"code": "...", "message": "...", "request_id": "..."}`，HTTP 状态码对应；
  参数校验失败固定为 `422 VALIDATION_ERROR`。

## 1. 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活探针，桌面守护使用 |
| GET | `/ready` | 就绪检查；未配置模型运行时返回 503 |
| GET | `/models` | 模型目录与默认模型 |
| GET | `/runtime` | 能力快照：就绪状态、chat/work 工具名、缓存统计 |

## 2. 模型服务与设置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/settings/providers` | 服务列表（密钥掩码）、模板、`setup_required` |
| POST | `/settings/providers` | 新建服务 |
| PUT | `/settings/providers/{id}` | 更新服务 |
| DELETE | `/settings/providers/{id}` | 删除服务 |
| POST | `/settings/providers/discover` | 用给定凭据请求 `{base_url}/models` |
| GET | `/settings` | 全局个性化开关 |
| PATCH | `/settings` | 设置全局个性化开关 |
| GET | `/settings/memory` | 记忆规划器配置（13 项，带 revision） |
| PATCH | `/settings/memory` | 按 revision 校验更新配置 |

## 3. 账户

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/account` | 本地资料：显示名、handle、头像版本 |
| PUT | `/account` | 更新显示名 / handle |
| GET | `/account/activity` | 使用统计 |
| GET | `/account/avatar` | 头像 PNG |
| PUT | `/account/avatar` | 上传头像（PNG，≤512 KiB） |
| DELETE | `/account/avatar` | 删除头像 |

## 4. 记忆、画像与图谱

默认后台 LLM 自动判断并沉淀有证据的持久信息。`/settings/memory` 的 `runtime`
返回实际 `engine=llm_learning`、模型可用性、有效模式、已生效字段、今日已上报与估计用量。
`auto/review/shadow/off`、模型、批量、置信度、观察来源、有效记忆上限和每日预算立即参与后续批次；
遗忘/TTL/保留与定期整合字段仍为预配置，不触发自动永久删除。

`/memory-learning` 增加 `engine`、`mode`、`dead`、`last_error`。模型不可用、坏 JSON、
证据不符和预算耗尽返回真实的持久化任务状态，不伪造画像；可通过重新分析恢复死信。
`/account/activity` 包含已上报的后台模型消费，新增 `memory_tokens` 与 `unreported_calls`；
每日数据的 `unreported` 标记缺失报告，前端以 ≥ 显示已知下界。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/memory-learning` | 学习状态：总数/待处理/已处理/失败、待确认数、画像版本 |
| POST | `/memory-learning/reprocess` | 重新分析最近已完成的轮次 |
| GET | `/memory-suggestions` | 待确认候选（含证据） |
| POST | `/memory-suggestions/{id}/review` | 确认 / 忽略候选（可带修改后的值） |
| GET | `/memories` | 记忆列表（`status`/`type`/`scope`，游标分页） |
| POST | `/memories` | 手工创建记忆 |
| GET | `/memories/{id}` | 读取单条记忆（有证据时一并返回） |
| PATCH | `/memories/{id}` | 修改标题/内容/来源/重要性/状态 |
| DELETE | `/memories/{id}` | 删除记忆（清理连线与画像引用） |
| POST | `/memories/{id}/feedback` | 追加 correct/confirm/reject 反馈 |
| GET | `/graph` | 记忆图谱（`limit`、`focus_id`），返回节点、边与总数 |
| PUT | `/graph/links` | 新建/更新连线（有向或无向、关系、相关度） |
| DELETE | `/graph/links` | 删除连线 |
| GET | `/profile` | 当前画像版本（preferences + attributes） |
| GET | `/profile/versions` | 版本历史（游标分页） |
| GET | `/profile/versions/{version}` | 指定历史版本 |
| PATCH | `/profile` | 按 `expected_version` 修改偏好（冲突返回 409） |

## 5. 工作区、会话与附件

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/workspaces` | 列出 / 创建工作区（映射真实目录） |
| GET/PATCH/DELETE | `/workspaces/{id}` | 读取 / 重命名 / 删除工作区 |
| POST/GET | `/threads` | 创建 / 列出会话（`mode`：`chat` 或 `work`） |
| GET/PATCH/DELETE | `/threads/{id}` | 读取 / 归档 / 删除会话 |
| GET | `/threads/{id}/messages` | 已归档轮次（含活动、指标、溯源） |
| DELETE | `/threads/{id}/messages` | 清空归档消息与未完成检查点 |
| POST | `/threads/{id}/attachments` | 上传附件（原始 body，`filename` 查询参数） |
| GET | `/attachments` | 附件库 |
| POST | `/threads/{id}/fork` | 分叉会话（复制历史与画像快照） |
| GET | `/threads/{id}/memory-receipts` | 某次运行产生的记忆/候选回执 |

## 6. 聊天与运行控制

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/chat` | 发送消息；返回 `ChatResult`（JSON）或 SSE 流 |
| POST | `/threads/{id}/resume` | 恢复暂停/中断的运行 |
| GET | `/threads/{id}/approval` | 读取该线程待审批批次 |
| POST | `/threads/{id}/approval/{approval_id}` | 批准或拒绝待审批批次 |

### `POST /chat` 主要字段

```json
{
  "thread_id": "uuid-or-null",
  "message": "帮我把方案整理成文档",
  "mode": "work",
  "model": "provider/model-id",
  "reasoning_effort": "medium",
  "stream": true,
  "project_id": "workspace-id-or-null",
  "approval_policy": "ask",
  "attachment_ids": ["file-id"],
  "personalization_enabled": true
}
```

- `thread_id: null` 表示新建会话；`mode` 固定工具集（`chat` 仅安全工具，`work` 全部工具）。
- 存在待审批时，非流式请求返回 **202** 与 `ToolReview`，而不是 `ChatResult`。
- 线程存在未完成运行时，新消息返回 `409 RUN_PENDING`；需先恢复或清理。

### `ChatResult`

```json
{
  "run_id": "…",
  "thread_id": "…",
  "response": "…",
  "profile_version": 4,
  "used_memory_ids": ["…"],
  "activity": [],
  "metrics": { "input_tokens": 1234, "cached_input_tokens": 900 }
}
```

### SSE 事件（`stream: true`）

帧格式为 `event: <name>\ndata: <json>\n\n`。

| 事件 | 载荷 | 时机 |
|---|---|---|
| `session` | `{thread_id}` | 流建立 |
| `delta` | `{text}` | 助手文本增量 |
| `activity` | `{run_id, items[], metrics}` | 运行活动变化（工具/模型/上下文步骤） |
| `approval_required` | `ToolReview` | 因工具审批暂停 |
| `done` | `ChatResult` | 运行完成 |
| `error` | `{code, message, request_id}` | 流式过程中出现 HTTP 错误 |

### 审批流程

1. `POST /chat`（或恢复）返回 `approval_required` 或 **202** + `approval_id`。
2. `GET /threads/{id}/approval` 读取同一审批（或 `null`）。
3. `POST /threads/{id}/approval/{approval_id}`，body `{"approved": true|false}`。
4. 运行继续：批准的调用执行，被拒绝的调用以带类型的错误回填给模型。

## 7. 示例

```powershell
# 健康检查
curl http://127.0.0.1:8000/api/v1/health

# 非流式聊天
curl -s -X POST http://127.0.0.1:8000/api/v1/chat ^
  -H "Content-Type: application/json" ^
  -H "X-Slothy-Token: <token>" ^
  -d '{"message":"你好"}'

# 流式聊天
curl.exe -N -X POST http://127.0.0.1:8000/api/v1/chat `
  -H "Content-Type: application/json" -H "X-Slothy-Token: <token>" `
  -d '{"message":"你好","stream":true}'

# 画像与版本
curl http://127.0.0.1:8000/api/v1/profile -H "X-Slothy-Token: <token>"
curl "http://127.0.0.1:8000/api/v1/profile/versions?limit=20"
```

由 Electron 启动后端时，端口取自后端 stdout 的 `{"event":"listening","port":N}`；
令牌只保存在主进程。

## 8. 说明

- API 为单用户设计（`local-user`）；多用户隔离是后续里程碑，客户端传入的 `user_id`
  永远不会被当作授权凭据。
- SSE 没有心跳与重连协议；连接断开即结束流，运行仍可从检查点恢复。
- 附件限制：单文件 2 MiB，单会话 24 MiB。

## 9. 连接器

| 方法 | 路径 | 请求 | 成功响应 |
| --- | --- | --- | --- |
| GET | `/connectors` | 无 | `200 {items: Connector[]}` |
| POST | `/connectors/import?kind=local\|mcp\|skill&filename=...` | `application/octet-stream` 原始文件 | `201 {items: Connector[]}`，默认关闭 |
| PATCH | `/connectors/{id}` | `{enabled: boolean}`，不接受字符串布尔值 | `200 Connector` |
| DELETE | `/connectors/{id}` | 无，仅可移除导入项 | `204` |

Connector 字段为 `id, kind, name, description, enabled, builtin, tools, transport, file_count`；不公开命令配置、环境变量、headers、凭证或解压路径。

本地工具支持 JSON 或含 `tool.json` 的 ZIP：`{name,description,command,args,input_schema}`。程序在当前工作目录运行，以 JSON stdin/stdout 通信；ZIP 中 `${plugin}` 指向清单目录。需预先安装解释器和依赖。MCP 接受标准 `{mcpServers:{name:{command,args,env}}}` 或 `{mcpServers:{name:{url,type,headers}}}`，HTTP type 为 `streamable-http` 或旧 `sse`。Skill ZIP 包含一个 `SKILL.md` 及参考文件，启用后按需注入，`read_skill_file(skill_id,path,offset=0)` 每次返回 8,000 字符与 `next_offset`；导入/启用不会执行脚本。

新增项默认关闭。MCP 开启时真实连接获取工具，失败保持原状态；关闭再开启可刷新定义。每个导入工具在聊天和工作模式均受审批约束；禁用撤销旧调用，定义改变会改变工具标识。凭证由 Windows DPAPI 保护。

错误沿用 `{code,message,request_id}`：404 `CONNECTOR_NOT_FOUND`；409 `CONNECTOR_EXISTS`；413 `CONNECTOR_TOO_LARGE`；422 `CONNECTOR_INVALID` / `CONNECTOR_LIMIT` / `VALIDATION_ERROR`；502 `CONNECTOR_CONNECTION_FAILED`。最大上传 10 MiB、解压 20 MiB / 512 项、64 个导入项、64 个同时启用的导入工具；Skill 最多启用 8 个、指导合计 64,000 字符。
