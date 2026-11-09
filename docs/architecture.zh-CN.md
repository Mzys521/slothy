# 架构

Slothy 是本地优先的桌面智能体。架构由四个约束决定：**模型在远端、数据留在本地**、
**每次运行可恢复**、**有副作用的工具需要人工审批**、**提示词前缀必须稳定**（否则缓存失效）。

## 1. 设计原则

1. **本地优先**：桌面应用只访问绑定 `127.0.0.1` 的本地后端；会话、记忆、画像与产物都是本地
   SQLite / 文件。
2. **边界清晰**：HTTP 层、应用编排、智能体图、持久化、模型适配与工具执行互相分离，依赖单向。
3. **确定性重放**：LangGraph 检查点按 super-step 落盘；审批与失败从同一状态恢复，而不是重来。
4. **副作用人工确认**：非安全工具会让运行暂停。
5. **记忆有证据**：记忆 = claim + 出处 + 作用域 + 审核历史；画像是版本化投影，不是隐藏黑盒。
6. **稳定前缀、易变尾部**：提示词按“变化频率”排序（见 §6），缓存命中是布局的属性，而不是运气。

## 2. 运行时拓扑

Chat / Work 会话独立运行。请求句柄、命令状态和流式正文按会话保存，切换页面不会丢失。
停止只取消对应会话的图执行；精确会话锁仍串行化同一对话的写入。进行中的任务仍属于当前后端进程。

HTML 回复继续使用双层沙箱，视口固定为窗口高度的 60%，限制在 240–560 CSS px，超长内容
内部滚动。SVG 图示保留响应式尺寸，中性填充、描边和渐变停靠点随外观变化，彩色语义与透明
内容保持。两类预览均不获得同源访问权限。

```
Electron 主进程                        Electron 渲染进程（Vue 3）
├─ BrowserWindow / 无边框窗口          ├─ 聊天 / 记忆 / 画像 / 图谱 / 设置页
├─ preload 桥（contextBridge）         ├─ fetch /api/v1 与 SSE 解析
├─ 每次启动生成的访问令牌              └─ 永远拿不到令牌
├─ 启动并守护后端
└─ 退出时回收后端
              │ 回环 HTTP（127.0.0.1:<随机端口>）
┌─────────────▼──────────────────────────────────────────────────────────────┐
│ uvicorn + FastAPI                                                          │
│  中间件：令牌校验 → Origin 校验 → request id                               │
│  路由 ──▶ AgentRunner ──▶ LangGraph online_agent                           │
│  服务：providers / conversations / workspace store / memory evolution      │
│  基础设施：检查点（SQLite）、模型适配、子进程执行器                         │
└─────────────┬──────────────────────────────────────────────────────────────┘
              │
      %APPDATA%\slothy\data（安装版）  /  core\data（开发）
      checkpoints.sqlite · checkpoints.sessions.sqlite ·
      checkpoints.workspace.sqlite · providers.json · artifacts/
```

关键性质：

- 后端端口由系统分配（`bind(("127.0.0.1", 0))`），通过 stdout 的
  `{"event":"listening","port":N}` 告知主进程。
- 主进程生成 32 字节随机令牌，经环境变量交给后端，并在 `/api/v1/*` 请求中注入；渲染进程
  永远拿不到令牌。
- 主进程监控后端进程；异常退出会提示并退出应用。安装版后端是冻结的可执行文件
  （`resources/backend/slothy-service.exe`）。
- Python 入口（`core/desktop/entry.py`）在桌面进程消失后自动退出，不残留孤儿进程。
- 渲染层在应用外壳之上保留首次启动引导层：仅当 `GET /api/v1/settings/providers` 返回
  `setup_required`（尚未配置 runner）时显示，保存服务商后关闭；配置读取失败时显示重试，
  而不是误判为“未配置”。

## 3. 后端分层

| 层 | 模块 | 职责 | 禁止 |
|---|---|---|---|
| HTTP / API | `app/api.py` | 路由、SSE 帧、请求模型、生命周期装配 | 承载智能体逻辑 |
| 应用服务 | `app/services/agent.py` | 运行编排、按线程串行、归档与恢复 | 依赖 SQLite 细节 |
| 智能体领域 | `app/agent/`（`graph`、`prompting`、`tools`、`providers`） | 图拓扑、节点、提示词计划、工具 schema | 打开数据库或读环境变量 |
| 记忆领域 | `app/personalization/` | claim 抽取、审核、画像投影 | 感知 HTTP 或图 |
| 基础设施 | `app/infrastructure/`（`checkpoints`、`conversations`、`workspace`、`llm`、`processes`） | SQLite 存储、会话归档、模型适配、受限子进程 | 导入图节点 |
| 工具逻辑 | `app/tools/` | 计算、命令、文档子进程、附件读取 | 导入 LangChain |
| 组装根 | `web/electron/*`、`core/desktop/entry.py` | 进程生命周期、配置、打包 | 承载业务规则 |

依赖方向单向：HTTP → 应用 → 领域 → 基础设施。图按
`(mode, model, effort, workspace directory)` 编译并缓存；工具在编译时绑定到模型。

## 4. 智能体图

```
START → prepare_run → decide → load_profile → retrieve_memories
      → build_personalized_context → agent ─┬─ 无工具调用 → finalize_response → END
                                            └─ 有工具调用 ──▶ tools ──▶ agent
                                                             │
                                          需要审批 ──▶ review_tools（interrupt）
                                                             ├─ 批准 → tools
                                                             └─ 拒绝 → agent
```

- **prepare_run**：校验新请求、清空本轮派生字段、按完整用户轮次限制工作历史。
- **decide**：可选地向独立决策模型请求受护栏约束的计划（路由/工具/上下文/恢复）；
  off、shadow、enforce 三档见 §4.1。
- **load_profile / retrieve_memories / build_personalized_context**：只读端口，分别读取画像
  快照、按作用域检索记忆、在预算内构建参考文本；可恢复的失败转成 warnings，而不是伪造回答。
- **agent**：通过提示词规划器组装请求（见 §6），带工具调用模型。
- **tools**：执行整批调用；**review_tools** 在批次包含非安全调用时用 `interrupt()` 暂停。
  批准后以 `Command(resume={approval_id: true})` 恢复；拒绝则向 agent 回填带类型的错误
  `ToolMessage`，让模型自行降级。
- **finalize_response**：只有非空、且无待执行工具调用的助手消息才算成功。

执行性质：

- 检查点：`AsyncSqliteSaver`（WAL、`synchronous=FULL`），每个 super-step 一份快照，
  `durability="sync"`。
- 存储线程键为 `[user_id, thread_id]`，业务 ID 保留在 state 中。
- Runner 按线程串行执行；推进前会补写上一轮缺失的归档。
- 审批策略（`ask` | `every`）决定哪些调用暂停；`ask` 下安全工具与只读命令不暂停。

### 4.1 决策模型驱动的决策层

`prepare_run` 与 `load_profile` 之间新增一个 `decide` 节点。决策模型在设置 → API 中从已配置
模型目录选择；密钥与地址复用所属 Provider，选择本身不含凭据。模型返回结构化计划：路由
（identity/model/tool）、工具子集、上下文预算与恢复提示。确定性护栏按模型目录、实时工具注册表、
审批要求和预算收敛计划；模型提议永远不能放宽审批或加入未知工具。未选择决策模型时由聊天模型
自主决策；已配置模型超时、非法 JSON 或预算不足时，控制权交回聊天模型。仅后端运行仍可使用
`DECISION_MODE` / `DASHSCOPE__*` 环境变量。计划与原因码写入图状态和指标，不进入 L1/L2 提示词。

## 5. 记忆与用户画像 V3

链路为：归档 → 持久学习队列 → LLM concepts/facts → 原话与作用域校验 → 事实事务 →
独立强化 → 画像/搜索/图谱投影。在线 Agent 保持只读，不引入 embedding 服务。

- `app/memory/learning.py` 保留后台编排及严格有界 JSON（最多 32 概念、16 事实）；
  `MemoryService` 复用原事务/审核生命周期并拥有 V3 写入，`MemoryStore` 拥有作用域检索
  和图谱投影。模型网络调用不持有写锁。
- 概念按用户和作用域保存类型、规范键及有证据的别名；事实保存主体、谓词、目标、值、
  证据、状态和版本链。语义边直接使用事实 ID。熟练度必须绑定具体技能，共现不代表关系成立。
- 概念和事实分别记录唯一会话强化事件；证据合格资格与实际强化分开。强度独立使用
  `f/(f+c)`，默认 c=4，不做集合归一化、TTL 或时间衰减。默认 500 条有效事实容量先去重，
  再归档未保护的低效用事实；身份、明确偏好和手动保护项不自动归档。
- 身份及个人技能全局可用，明确项目声明局部可用。过滤作用域后组合 FTS5、中文二元组、
  英文词项、别名和身份意图，一跳扩展关联事实，按 `.75 相关性 + .15 关系强度 + .10 重要性`
  排序。无关高频技能不注入；默认 8 条与概念元数据共享 1200 个性化 token 预算。
- 事实是唯一真相；手动和自动都投影 `identity/skill/preference/habit/goal` attributes，
  取消可独立写入的 profile_json/preferences。画像乐观编辑创建新事实版本；永久删除清除
  证据及所有历史画像正文。
- 直接身份问答跳过自由生成，读取实时全局事实；缺失、待学习、待确认、冲突、关闭和读取失败
  分别提示。有界 memory_trace 保存提供的证据 ID、关系路径、分数和原因，不复制正文，
  不把上下文提供等同于模型采纳。
- 持久队列 generation、配置修订、来源排除和 SQLite 唯一事件阻止迟到结果与重放污染。
  网络错误及每日预算预留沿用可观测的后台生命周期。

## 6. 提示词分层与缓存

2026-11-09 更新参照 DSH 区分静态提示分节与动态运行快照：静态内容按 order/name 组装；
Skill、显式引用和附件在独立检查点字段保存请求表面，不混入真实消息、归档、轮次或记忆学习。
内容相同时复用位置，变化时在当前历史后追加；压缩移除锚点后重投最新快照。每次调用重验来源，
撤销和作用域变化会移除旧正文；默认最多 8 快照和 32768 字节，超大单个快照只用于当次请求。
长期记忆服务、画像及个性化检索的注入和撤销逻辑保持原行为。DeepSeek 仅回传工具往返需要的
assistant 推理，最终回答推理留在显示和归档。上下文统计不再重复计数系统提示或误认尾部引用。
默认提示版本 `2026-11-09.context-v1`；离线前缀比例表示复用条件，实际缓存命中仍以厂商 usage 为准。

请求按“变化频率”组织为三层：

| 层 | 内容 | 变化频率 | 缓存策略 |
|---|---|---|---|
| **L1** | 版本化系统指令、排序后的工具说明/schema、会话冻结画像快照 | 每会话 | 稳定前缀；Anthropic 上设置显式断点 |
| **L2** | 对话历史（只追加）、周期性摘要 | 追加 / 偶发压缩 | 前缀随轮次增长 |
| **L3** | 当轮检索记忆、画像增量、附件引用、当前用户消息 | 每轮 | 尾部；不缓存 |

由此得到的设计规则：

- 易变参考必须放在历史**之后**，否则每轮都会让整个前缀失效。
- 历史按阈值批量压缩（而非每轮裁掉一轮）；压缩是一次显式的缓存重置事件，摘要带版本号。
- 画像快照在会话开始时冻结；后续学习通过 L3 增量进入当前会话，新会话才读取最新快照。
- 运行指标记录 provider 报告的缓存 token、分段哈希与字节数，命中率变化可度量。

## 7. 持久化

| 文件 | 归属 | 代表表 |
|---|---|---|
| `checkpoints.sqlite` | LangGraph 执行状态 | checkpoints / writes |
| `checkpoints.sessions.sqlite` | 会话归档、线程、偏好、账户、附件、记忆配置 | conversation_turns / threads / preferences / local_accounts / conversation_files |
| `checkpoints.workspace.sqlite` | 记忆、claim、证据、画像、关系、提示词快照、检索缓存 | workspace_memories / memory_claims / memory_evidence / workspace_profiles / workspace_memory_links |
| `providers.json` | 模型服务配置 | DPAPI 加密密钥、模型目录 |

全部使用 WAL；需要时启用外键；结构升级为打开时执行的幂等 `ALTER TABLE` 迁移。

## 8. 工具与执行边界

`ToolDescriptor → ToolRegistry → ToolGateway → ToolNode → 执行器` 是内置、附件和连接器工具
的统一装配路径。描述符决定参数、模式、平台、审批、静态说明和限制。模型不绑定隐藏别名，
运行时名单保留注册顺序，提示词说明与 schema 按名称排序。独立计算/执行辅助函数保留原生接口。

模型统一收到 `ToolResult`：`ok/summary/data/artifacts/next_offset/error`，错误带稳定 code、
retryable 与修正提示。默认审查 write/execute/network；`every` 审查全部可用且参数有效的调用。
Skill 引用读取在两模式免审，导入执行连接器限定 Work。审批卡片展示风险、影响路径和结构化参数。

批次顺序执行，默认每轮最多 12 次、64,000 输出字符、120 秒工具执行、同工具连续失败 3 次。
预算从当前 run 的检查点结果恢复，审批等待单独计时；耗尽时仍回填有界错误以保持调用/结果配对。
`RunMetrics.tools` 进入 SSE、归档和按日/工具账户聚合；`usage_reported=false` 区分模型未报告
用量与实际零值。

| 工具 | 执行方式 | 边界 |
|---|---|---|
| `calculate` / `calculate_financial` | 进程内 | float / Decimal 字符串计算 |
| `workspace_read` | 进程内 | 有界 UTF-8 分页、目录与固定字符串搜索；排除私密和链接路径 |
| `workspace_write` | 进程内 | 审批后创建目录/写文件；默认不覆盖 |
| `workspace_delete` | 进程内 | 审批后的 Chat/Work 文件/目录删除；拒绝符号链接、根目录与私有路径；执行时复验递归目标 |
| `deepseek_search` | HTTPS | 官方 DeepSeek Search API；解析到搜索/模型密钥后才注册；结构化来源与超时/限流/鉴权错误 |
| `export_document` | 标准库 / python-docx | 审批后模板导出 md/txt/csv/json/docx，不执行模型代码 |
| `run_python` | 独立 Python 进程 | 审批、调用清单、独立目录、原有审计和产物限制 |
| `get_datetime` | 进程内 | 当前时间和带偏移量的 IANA 时区换算，随包携带 tzdata |
| `list_uploaded_files` / `read_uploaded_file` | 进程内 SQLite 读取 | 按线程隔离、分页读取 |

文件副作用先独占写入调用清单，再执行；完成结果校验路径、大小和 SHA-256 后复用。
状态不确定或产物改变时返回带清单路径的 `RECOVERY_REQUIRED`，不自动重复副作用。
默认保留最近 100 个已完成/已失败的受管调用目录，保留不确定状态和工作区业务文件。
文件与 SQLite 仍不是原子事务。隐藏旧名仅恢复升级前运行，不进入模型绑定和运行时名单。
`2026-11-07.memory-v3` 合并本轮工具变更，提示词前缀有意失效一次。

子进程保持无 Shell 的 argv、最小环境、输出上限、超时终止和取消清理。
Python 审计是策略级约束，不是操作系统沙箱。

可选 `web_fetch` 默认关闭，需同时配置域名允许列表才向模型暴露；仅接受无凭据的 HTTPS/443。
检查全部 DNS 地址与每次重定向，连接固定到已校验公网 IP，同时使用原域名做 TLS 验证。
响应字节、文本和重定向次数有界，HTML 提取不含脚本/样式；该设置与已导入 MCP 的配置 URL 分开。

## 9. 连接器

连接器让工具面可扩展且无需重启应用。

| 类型 | 导入格式 | 执行模型 |
|---|---|---|
| 本地工具 | JSON，或含 `tool.json` 的 ZIP | argv 命令、JSON stdin/stdout、工作区为 cwd、`${plugin}` 指向包目录 |
| MCP 服务 | `mcpServers` JSON（stdio `command,args,env`；HTTP `url,type,headers`） | 通过真实连接发现（`streamable-http` 或兼容 `sse`）；调用需审批 |
| Skills | 含唯一 `SKILL.md` 与引用文件的 ZIP | 低信任 L3 指令；`read_skill_file` 分页读取 UTF-8 引用 |

导入项默认停用；启用时通过真实连接发现工具，失败保留原状态。导入执行工具仅在 Work 模式可见且
需要审批，停用立即撤销执行权限，定义变化会改变工具标识。配置、凭据、环境变量与解压路径永远不会
由 API 返回；Windows 下连接器配置使用 DPAPI 保护。限制：单次导入 10 MiB、解压后 20 MiB / 512
项、最多 64 个导入项与 64 个启用的导入工具、8 个启用 Skills（指令合计 64,000 字符）。本地工具
与 MCP 命令所需的解释器和依赖必须已安装；导入或启用不会执行包内脚本。

## 10. 安全模型

| 关注点 | 控制 |
|---|---|
| 本地 API | 回环绑定、启动令牌、Host/Origin 校验 |
| 渲染进程 | `contextIsolation`、`sandbox`、无 `nodeIntegration`、严格校验 IPC 发送方 |
| 模型密钥 | Windows DPAPI、API 只返回掩码、不写日志 |
| 工具副作用 | 非安全工具审批门、白名单、路径检查、输出预算 |
| 数据保留 | 显式删除接口、历史画像擦除、按记忆删除 |
| 推理隐私 | 只发送到用户自己配置的模型端点 |

## 11. 图表

- [`diagrams/architecture-overall.svg`](diagrams/architecture-overall.svg) — 进程与分层总览
- [`diagrams/sequence-chat.svg`](diagrams/sequence-chat.svg) — 一次聊天（含工具审批）
- [`diagrams/sequence-memory-update.svg`](diagrams/sequence-memory-update.svg) — 记忆学习与画像投影
- [`diagrams/er-diagram.svg`](diagrams/er-diagram.svg) — 持久化关系
- [`assets/diagrams/runtime-overview.svg`](assets/diagrams/runtime-overview.svg) — 运行时拓扑
- [`assets/diagrams/backend-modules.svg`](assets/diagrams/backend-modules.svg) — 后端模块图

## 12. 已知限制

- 打包以 Windows 为先，安装包未签名；其他平台仅用于开发。
- 工具与连接器执行是策略级约束，不是操作系统沙箱；连接器命令以用户权限运行，且需要预装解释器与依赖。
- LLM 记忆学习引擎已启用（`auto` / `review` / `shadow` / `off`），但定时整合、衰减、TTL 与
  永久删除仍是预留配置，不会自动执行。
- 记忆检索目前是词法匹配；embedding 是明确的后续工作。
- 对话分支已有设计文档，尚未实现。

## V3 数据纪元

开库前 `prepare_storage` 以固定三库白名单、独占锁和原子标记完成一次性重置；保留独立配置
及真实工作区文件。`.storage.json` 在开新库前写入，重启不会再次删除新业务数据。前端按
`/runtime.data_epoch` 清理旧会话缓存，不导入旧消息。冻结画像每次调用前复核事实 ID、状态
和值，更正/归档/删除会剔除旧值，新值进入 L3；审批恢复亦复核。
