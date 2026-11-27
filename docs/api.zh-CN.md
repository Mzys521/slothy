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
| GET | `/storage/recovery` | 启动存储状态及原数据是否可保留 |
| POST | `/storage/recovery/adopt` | 备份并继续使用符合条件的原数据库 |
| POST | `/storage/recovery/reset` | 验证备份后初始化新库 |

## 2. 模型服务与设置

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/settings/providers` | 服务列表（密钥掩码）、模板、`setup_required` |
| POST | `/settings/providers` | 新建服务 |
| PUT | `/settings/providers/{id}` | 更新服务 |
| DELETE | `/settings/providers/{id}` | 删除服务 |
| POST | `/settings/providers/discover` | 用给定凭据请求 `{base_url}/models` |
| POST | `/settings/providers/test-models` | 用真实小请求验证所选模型，支持部分成功 |
| GET | `/settings/decision` | 模型选择、可用模型 ID、模式、来源与可用性原因 |
| PUT | `/settings/decision` | 选择/清除决策模型并重建 Runner |
| GET | `/settings/search` | DeepSeek 搜索凭据状态（不含密钥） |
| PUT | `/settings/search` | 保存独立搜索密钥 |
| DELETE | `/settings/search` | 删除独立搜索密钥 |
| POST | `/settings/search/test` | 发起一次真实搜索测试 |
| GET | `/settings` | 全局个性化开关 |
| PATCH | `/settings` | 设置全局个性化开关 |
| GET | `/settings/memory` | 已保存与已生效版本、待应用状态和容量 |
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
| GET | `/profile` | 当前画像版本与统一 attributes |
| GET | `/profile/versions` | 版本历史（游标分页） |
| GET | `/profile/versions/{version}` | 指定历史版本 |
| PATCH | `/profile` | 按 `expected_version` 替换 attributes（冲突返回 409） |

### V3 契约（0.1.4.1-beta）

`PATCH /profile` 接收 `{expected_version, attributes: [{key, dimension, value,
scope?, scope_ref?}], change_summary?}`，保留 value 的 JSON 类型。维度为
`identity/skill/preference/habit/goal`。手动和自动都写事实，画像只作投影；不再接受
`preferences`。姓名同义键统一为 `identity.name`，称呼为独立的 `identity.preferred_name`，
身份固定 global。技能使用 `skills.<target>.level`（beginner/intermediate/advanced/expert）
或 `.learning`（布尔值），必须保留目标对象；个人技能默认全局，明确项目声明保留局部作用域。

记忆新增 `memory_key, dimension, value, subject, predicate, concepts, concept_ids,
source_concept_id, target_concept_id, effective_frequency, strength, protected,
superseded_by, scoring_version`。状态为 `active/archived/superseded/invalidated`，更正返回
新事实 ID。强度为 `f/(f+strength_c)`，默认 c=4。只有合格证据参与强化，概念按作用域和会话去重，
关系按会话去重；容量治理归档未保护事实，不按分数永久删除，也不执行 TTL 或时间衰减。

`GET /graph?project_id=...&thread_id=...` 只返回可见范围的概念节点及独立频次；有向语义边包含
`fact_id, relation, effective_frequency, strength, evidence_ids`，同节点不同谓词不会覆盖。
`GET /concepts/{id}` 使用相同可见性检查。手动连线保留原接口，共现不再自动创建语义边。
`/memory-learning.statistics` 返回有效/归档数量、强化去重、冲突和容量阻塞计数。

聊天、历史与审批响应新增 `memory_trace`、`response_source`。`used_memory_ids` 表示实际提供给
模型或身份直答器的证据，不表示模型确定采纳。诊断只保存有界 ID、分数、关系路径和原因码，
不复制正文。直接身份问答跳过生成，从有效全局身份事实直答；缺失、待学习、待确认、冲突、关闭、
读取失败分别提示。

`/runtime` 返回 `storage_version` 和 `data_epoch`。Slothy 0.1.6 在打开已有业务库前核验结构。
需要恢复时，普通路由返回 `503 STORAGE_RECOVERY_REQUIRED`，存储恢复接口仍可调用。恢复动作
返回 `restart_required: true` 时，在重启后端后生效。

## 5. 工作区、会话与附件

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/workspaces` | 列出 / 创建工作区（映射真实目录） |
| GET/PATCH/DELETE | `/workspaces/{id}` | 读取 / 重命名 / 删除工作区 |
| POST/GET | `/threads` | 创建 / 列出会话（`mode`：`chat` 或 `work`） |
| GET/PATCH/DELETE | `/threads/{id}` | 读取 / 归档 / 删除会话 |
| GET | `/threads/{id}/messages` | 已归档轮次（含活动、指标、溯源） |
| DELETE | `/threads/{id}/messages` | 清空归档消息与未完成检查点 |
| POST | `/threads/{id}/attachments` | 上传原始字节，查询参数含 `filename` 和可选 `file_request_id` |
| GET | `/attachments` | 附件库 |
| POST | `/threads/{id}/fork` | 分叉会话，继承历史、画像快照与产物链接 |
| GET | `/threads/{id}/memory-receipts` | 某次运行产生的记忆/候选回执 |
| GET | `/operations/{id}` | 查看清理操作状态及已完成步骤 |

清空消息、删除会话和删除工作区在清理完成后返回 `204`。收到 `202` 时，响应包含
`operation_id`、`state` 和 `error`。可查询 `/operations/{id}`，直到 `state` 为
`done` 或 `failed`。清理任务在重启后仍保留。

在同一会话内复用 `file_request_id` 可取得已保存的附件。无法读取的文档返回
`422 ATTACHMENT_UNREADABLE`。历史轮次可用 `missing: true` 标记缺失附件。

## 6. 聊天与运行控制

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/chat` | 发送消息；返回 `ChatResult`（JSON）或 SSE 流 |
| POST | `/threads/{id}/resume` | 恢复暂停/中断的运行 |
| GET | `/threads/{id}/run-status` | 按 `client_request_id` 或 `run_id` 对账最新运行 |
| GET | `/threads/{id}/approval` | 读取该线程待审批批次 |
| POST | `/threads/{id}/approval/{approval_id}` | 批准或拒绝待审批批次 |

### `POST /chat` 主要字段

```json
{
  "thread_id": "uuid-or-null",
  "client_request_id": "stable-id-for-this-submission",
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
- 向已有会话重复发送同一提交时，应使用稳定的 `client_request_id`。已经完成的请求会返回保存的
  结果。用同一 ID 提交不同的消息、附件或引用会返回 `409 REQUEST_CONFLICT`。

### 检查点恢复

`GET /threads/{id}/messages` 增加 `pending_run`（或 `null`），从原生 checkpoint 投影原请求、
run ID、checkpoint ID、保存的模型选择、附件元数据、引用和活动。应用重启且无浏览器缓存时
也能找回未完成轮次；已完成检查点缺失的归档会幂等补写。

使用 `GET /threads/{id}/run-status` 对账最新运行。状态包括 `not_started`、`pending`、
`awaiting_approval`、`completed` 和 `failed`，已完成时会附带保存的结果。没有配置模型时，
仍可读取检查点和待审批记录。

从检查点继续时，向 `/threads/{id}/resume?stream=true&run_id=<运行ID>&checkpoint_id=<预期ID>`
发送空请求体。两个查询参数可用于校验身份与并发状态。运行不符返回 `409 RUN_MISMATCH`，
检查点已变更返回 `409 CHECKPOINT_STALE`。恢复保留原始输入，人工审批继续使用专用接口。
没有待继续运行且未指定运行身份时，返回 `409 NO_PENDING_RUN`。指定匹配的已完成运行，可在
回执丢失后取回其结果。

工具批次内已完成调用由 LangGraph task 保存，恢复时不重复执行。被中断的模型节点从节点
边界重新生成，不支持 token 级接续。`/retry` 和“从检查点继续”使用此路径，不会静默重发
中断请求。

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
- 附件限制：单文件 32 MiB，单会话 128 MiB。文件先完整接收；提示词使用有界预览，`read_uploaded_file` 从 SQLite 按 8,000 字符分页读取。

## 9. 连接器

表中路径以 `/api/v1` 为前缀。

| 方法 | 路径 | 请求 | 成功响应 |
| --- | --- | --- | --- |
| GET | `/connectors` | 无 | `200 {items: Connector[]}` |
| GET | `/connectors/{id}` | 无 | `200 {item, tools, mcp, skill}` |
| POST | `/connectors/import?kind=local\|mcp\|skill&filename=...` | `application/octet-stream` 原始文件 | `201 {items: Connector[]}`，默认关闭 |
| PATCH | `/connectors/{id}` | `{enabled: boolean, revision?: string}` | `200 Connector` |
| DELETE | `/connectors/{id}` | 导入项或表单创建项 | `204` |
| POST | `/connectors/mcp` | MCP 表单 | `201 Connector` |
| PUT | `/connectors/{id}/mcp` | MCP 表单 | `200 Connector` |
| POST | `/connectors/mcp/test` | `{config, confirmed, connector_id?, revision?}` | `200 MCPResult` |
| POST | `/connectors/{id}/test` | `{confirmed, revision?}` | `200 MCPResult` |
| POST | `/connectors/{id}/refresh` | `{confirmed, revision?}` | `200 MCPResult`，成功时含更新后的 `connector` |
| POST | `/connectors/skills` | Skill 表单 | `201 Connector` |
| PUT | `/connectors/{id}/skill` | Skill 表单 | `200 Connector` |

列表保留 `id, kind, name, description, enabled, builtin, tools, transport, file_count, risk, platforms, available, capabilities`。新增可选字段 `revision`、Skill 的 `reference_count`、MCP 的 `connection_status`。详情响应使用 `Cache-Control: no-store`。工具定义包含 `name, description, input_schema`。MCP 详情返回编辑字段，已保存的环境变量和请求头仅返回键名及 `null` 值。Skill 详情包含完整 `markdown`、可编辑 `instructions` 和相对 SKILL.md 的参考文件路径。

MCP 表单接受 `{name, description?, enabled?, revision?, config, confirmed?}`。stdio 使用 `config.type: "stdio"` 和 `command`，可选 `args, cwd, env`。绝对路径 `cwd` 同时用于发现和调用。HTTP 或 SSE 使用 `config.type: "streamable-http" | "sse"` 和 `url`，可选 `headers, timeout`，超时可设为 1 至 30 秒的整数。提交 `null` 保留已存值，提交新值可替换，省略键可删除。请求头键名按大小写不敏感匹配。Windows 使用 DPAPI 保护保存的配置。

测试、刷新和保存时启用均使用原有本地认证，并要求显式 `confirmed: true`。MCP 客户端实际完成初始化和工具发现。探测响应为 `{ok, response_status, tool_count, tools, code, message, http_status, connector}`，其中 `response_status` 为 `tools_listed` 或 `failed`，端点拒绝请求时可返回 HTTP 状态码。测试保留原有工具定义与启用状态。刷新保存发现的定义，保留启用状态。刷新或启用保存失败时保留原有记录。连接状态取当前进程最近一次检查结果，应用重启后启用的服务先显示待验证。Agent 在工作模式调用 MCP 工具时仍需 Runtime 审批。编辑 MCP 会更新工具标识，避免旧审批作用于编辑后的连接。

Skill 表单接受 `{name, description?, instructions, enabled?, revision?, files?, remove_files?}`。新文件为 `{name, data}`，只接受文件名，内容使用 base64 编码。服务端将文件写入 `references/`，生成 YAML Front Matter，并通过原有注册表提交替换后的技能包。`remove_files` 为待移除的现有参考路径。已导入的 Skill 可用原 ID 编辑。启用后的指令作为低信任度参考进入提示词。`read_skill_file(skill_id,path,offset=0)` 读取不超过 1 MiB 的 UTF-8 文本，每页 8,000 字符。创建、导入和启用 Skill 均不执行包内脚本。

本地工具继续支持 JSON 和 ZIP 内的 `tool.json`，程序在当前工作目录使用 JSON stdin/stdout，`${plugin}` 表示包目录。MCP 继续支持标准 `mcpServers` JSON，Skill ZIP 需包含一个 `SKILL.md`。导入后默认关闭，新添加的 MCP 和 Skill 在同类型中需使用唯一名称。

校验与持久化错误沿用 `{code,message,request_id}`。主要状态包括 404 `CONNECTOR_NOT_FOUND`，409 `CONNECTOR_EXISTS`、`CONNECTOR_CHANGED` 或 `CONFIRMATION_REQUIRED`，413 `CONNECTOR_TOO_LARGE`，422 `CONNECTOR_INVALID` 或 `VALIDATION_ERROR`。原启用接口保留 `CONNECTOR_LIMIT` 和 `CONNECTOR_CONNECTION_FAILED`。单次导入或新增参考文件上限为 10 MiB，每包最多 512 项和 20 MiB，最多保存 64 个连接器、同时启用 64 个导入或表单创建的工具及 8 个 Skill，启用的 Skill 指令合计最多 64,000 字符。

## 统一工具契约

审批调用增加 `irreversible` 标记：删除、覆盖写入及明确声明不可逆的工具使用高危样式。
删除使用 `risk: "delete"`，普通写入仍为 `risk: "write"`。原有审批要求保持不变。

`GET /runtime` 增加按 read/write/delete/execute/network 分组的 `tool_groups` 和
`deprecated_tools`。默认桌面端 Work 10 个、Chat 8 个内置工具；Skill 引用读取按启用状态提供，
`deepseek_search` 仅在解析到搜索密钥后出现，`web_fetch` 为可选项，隐藏旧名不进入可见清单。
`workspace_delete` 在 Chat/Work 均需审批：路径限制在工作区，拒绝链接与根目录，执行时复验
递归目标。连接器列表包含 `risk/platforms/available` 与逐工具 `capabilities`。
已认证的详情接口提供编辑字段，并隐藏已保存的环境变量值和请求头值。

### DeepSeek 搜索

`GET /settings/search` 返回 `{configured,source,independent}`，不回传密钥；
`PUT /settings/search` 保存独立 DPAPI 加密密钥，`DELETE` 仅删除独立密钥，
`POST /settings/search/test` 发起一次真实搜索测试。可选 `deepseek_search` 工具调用
`POST https://api.deepseek.com/v1/tools/search`，参数为 `query/count/ori_question/
need_content`；结果包含标题、URL、摘要、正文、站点、发布时间、权威性与排序分数。
未配置独立密钥时复用 DeepSeek 模型密钥；两者都没有则不注册工具。

模型工具消息统一为 `ToolResult`：`ok/summary/data/artifacts/next_offset/error`。失败的 `error` 含 `code/message/retryable/hint`。错误码包括 `INVALID_ARGS/UNKNOWN_TOOL/
UNAVAILABLE/OUT_OF_WORKSPACE/OUTPUT_TOO_LARGE/TIMEOUT/DENIED/CONNECTOR_DISABLED/
EXEC_FAILED/NETWORK_BLOCKED/BUDGET_EXCEEDED/RECOVERY_REQUIRED`。
分页用 `next_offset`；产物包含 `id/name/bytes/rel_path/sha256/url`。
`ChatResult`、done SSE 帧与归档 `ThreadMessage` 增加 `reasoning`；它仅用于展示，不进入
提示词或历史消息正文；流式响应用独立 `reasoning` 事件与 `delta` 分开传输。
`GET /threads/{thread_id}/artifacts/{artifact_id}` 校验归属、路径、大小和摘要后下载真实产物，
不可用时返回 404。

审批项可增加 `risk` 与 `review_hint`；中断 ID、严格布尔决定和整批审批语义不变。
不可用或参数无效的调用直接返回工具错误；`every` 审查全部有效可用调用。
导入执行工具仅在 Work 可见且需要审批，Skill 引用在两模式免审。

SSE done、归档与账户活动包含 `metrics.tools`，字段为 `name/calls/ok/errors/rejected/
approval_wait_ms/duration_ms/output_bytes/output_chars/artifacts/timed_out/reused`。
调用数包含已结算尝试与拒绝，`ok + errors + rejected = calls`。批次审批的墙钟等待分别记录到
各调用，不计执行预算；复用仍记一次逻辑调用，沿用清单保存的执行耗时。
只有工具指标而没有模型用量时以 `usage_reported=false` 标记，不把占位零值当作真实账单。
`GET /account/activity` 增加新埋点运行的 `tools/tool_daily`；旧活动保留原有合计计数。

配置见 `.env.example`：工具开关、网络显式启用及 JSON 域名允许列表、运行预算、
文件/搜索上限和受管产物保留数。`web_fetch` 需启用且允许列表非空，仅 HTTPS/443，
每次 DNS 与重定向都校验。

`python -m examples.evaluate_tool_golden` 离线校验 25 个固定用例，不调用模型；
`--live` 才请求已配置模型生成工具提议并对比历史工具集，不真正执行工具。
报告区分离线/schema 校验、全量任务路由覆盖和共同能力上的选择准确率，不冒充任务执行成功率。

## 输入区命令与显式引用

输入 `/`、`@` 与加号使用同一展开栏。20 个封闭命令复用已有会话/工作区状态、模型选择、
设置、附件上传及 SSE；未知命令和非法参数不发送给模型，并保留草稿供修正。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/references/folders`、`/references/files` | 有界工作区路径及当前会话附件搜索 |
| GET | `/references/sessions`、`/references/plugins` | 所属会话及插件公开元数据 |
| POST | `/threads/{id}/compact` | 明确确认后压缩 L2，保留最近两轮及完整归档 |
| POST | `/threads/{id}/export` | 明确确认后通过既有模板导出 md/docx |
| POST | `/commands/events` | 记录封闭命令身份，不接受执行参数 |
| GET | `/commands/usage` | 本地命令计数与引用预检结果 |

搜索参数为 `q`（最多 120 字）、`limit`（1–8）、可选 `thread_id`、`workspace_id=personal`、
文件夹浏览用的 `path=.`，返回 `{items:[]}`。沿用 token、Origin 与归属校验；搜索不可用返回
`REFERENCE_UNAVAILABLE`，不存在的会话仍返回既有 404。

`POST /chat` 新增可选 `references[]`，最多 8 个不同对象：

```json
{"message":"总结所选资料","thread_id":"...","references":[
  {"kind":"folder","path":"reports"},
  {"kind":"file","id":"reports/notes.md","source":"workspace"},
  {"kind":"file","id":"attachment-id","source":"attachment"},
  {"kind":"session","id":"owned-session-id"},
  {"kind":"plugin","id":"connector-id"}
]}
```

服务端按真实身份解析，非法引用在进入模型前返回 422 `REFERENCE_INVALID`。每次模型调用重新校验，
引用仅为低信任 L3 数据，不启用插件、不改变审批。总预算 `REFERENCES_CONTEXT_CHARS` 默认 12k 字符，
与记忆独立；文件夹/文件/会话/插件单块最多 4k/8k/4k/2k，二进制只提供元数据。会话优先已有摘要
及最近两轮摘录，无摘要时最多六轮。完成结果、审批和归档含 `references` 与 `used_reference_ids`；
历史 chip 标注失效、停用或截断，不将引用全文写入 L1/L2 或归档。
完成结果还包含服务器保存的原始 `message`，恢复时用于校正消息关联，避免将回答归到修改后的草稿。

压缩请求为 `{"confirmed":true}`；导出还接受 `format:"md"|"docx"` 和可选 UUID `request_id`，
用于既有清单复用。未确认返回 409 `CONFIRMATION_REQUIRED`；未完成运行不能压缩。文件名由服务端生成，
下载沿用产物端点。命令不计工具调用或模型用量；`command_events/reference_events` 只保存本地元数据，
没有预检时成功率为未知。`/stop` 取消当前请求，`/retry` 可通过既有 resume 接口恢复。
