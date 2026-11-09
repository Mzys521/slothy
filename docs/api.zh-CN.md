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
| POST | `/settings/providers/test-models` | 用真实小请求验证所选模型，支持部分成功 |
| GET | `/settings/decision` | 决策模型选择与可用模型 ID |
| PUT | `/settings/decision` | 选择/清除决策模型并重建 Runner |
| GET | `/settings/search` | DeepSeek 搜索凭据状态（不含密钥） |
| PUT | `/settings/search` | 保存独立搜索密钥 |
| DELETE | `/settings/search` | 删除独立搜索密钥 |
| POST | `/settings/search/test` | 发起一次真实搜索测试 |
| GET | `/settings` | 全局个性化开关 |
| PATCH | `/settings` | 设置全局个性化开关 |
| GET | `/settings/memory` | 记忆规划器配置（14 项，带 revision） |
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

`/runtime` 新增 `storage_version=3` 和 `data_epoch`。首次启动 V3 仅一次重置三份业务数据库，
不回放旧消息；模型/连接器配置文件、界面偏好和工作区实际文件保留。前端数据纪元变化时清除
旧会话缓存，不再向新业务库导入旧浏览器消息。

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

## 统一工具契约

`GET /runtime` 增加按 read/write/execute/network 分组的 `tool_groups` 和
`deprecated_tools`。默认桌面端 Work 10 个、Chat 8 个内置工具；Skill 引用读取按启用状态提供，
`deepseek_search` 仅在解析到搜索密钥后出现，`web_fetch` 为可选项，隐藏旧名不进入可见清单。
`workspace_delete` 在 Chat/Work 均需审批：路径限制在工作区，拒绝链接与根目录，执行时复验
递归目标。连接器增加 `risk/platforms/available` 与逐工具 `capabilities`；配置、凭据、
环境变量和包路径不公开。

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
