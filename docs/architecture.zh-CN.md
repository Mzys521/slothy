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
START → prepare_run → load_profile → retrieve_memories
      → build_personalized_context → agent ─┬─ 无工具调用 → finalize_response → END
                                            └─ 有工具调用 ──▶ tools ──▶ agent
                                                             │
                                          需要审批 ──▶ review_tools（interrupt）
                                                             ├─ 批准 → tools
                                                             └─ 拒绝 → agent
```

- **prepare_run**：校验新请求、清空本轮派生字段、按完整用户轮次限制工作历史。
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

## 5. 记忆与用户画像

```
完成一轮 ──▶ 归档（sessions.sqlite）──▶ 学习队列
                                        │
                 规则抽取（偏好/习惯/目标/事实/情景）
                                        ▼
                        memory_claims + memory_evidence（workspace.sqlite）
                                        │
                     审核（确认/修改/忽略）──▶ 已确认记忆
                                        │
                  画像投影（偏好/习惯/目标）──▶ 画像新版本 +1
                                        │
                   下一轮：按作用域检索 + 注入画像快照
```

- claim 带作用域（`global` / `project` / `thread`）、维度、尽可能稳定的主题键与证据原文。
- 画像是不可变版本链；编辑使用乐观并发（`expected_version`），删除会擦除旧快照原文，只有
  内容变化才产生新版本（保留最近 200 版）。
- 记忆关系（`shared_conversation`、手工连线）保存在同一工作区库；图谱 API 支持有向/无向
  连线与删除。
- 工作区映射到真实目录；工具实例与产物根按目录隔离，不改变进程工作目录。这是路径策略，
  不是操作系统沙箱。

## 6. 提示词分层与缓存

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

| 工具 | 执行方式 | 边界 |
|---|---|---|
| `calculate` / `calculate_financial` | 进程内 | float / Decimal 运算 |
| `run_command` | 白名单命令进程内，`rg` 子进程 | 命令枚举、argv 列表、工作区路径、无 shell |
| `write_document_python` | 独立 Python 进程 | 独立 run 目录、审计策略、产物预算 |
| `list_uploaded_files` / `read_uploaded_file` | 进程内 SQLite 读取 | 按线程隔离、分页读取 |

子进程使用 `create_subprocess_exec`（仅 argv）、最小环境白名单、合并 stderr、输出上限与超时
终止。文档生成有策略检查，但**不是**操作系统沙箱；这是明确的设计边界。

## 9. 安全模型

| 关注点 | 控制 |
|---|---|
| 本地 API | 回环绑定、启动令牌、Host/Origin 校验 |
| 渲染进程 | `contextIsolation`、`sandbox`、无 `nodeIntegration`、严格校验 IPC 发送方 |
| 模型密钥 | Windows DPAPI、API 只返回掩码、不写日志 |
| 工具副作用 | 非安全工具审批门、白名单、路径检查、输出预算 |
| 数据保留 | 显式删除接口、历史画像擦除、按记忆删除 |
| 推理隐私 | 只发送到用户自己配置的模型端点 |

## 10. 图表

- [`diagrams/architecture-overall.svg`](diagrams/architecture-overall.svg) — 进程与分层总览
- [`diagrams/sequence-chat.svg`](diagrams/sequence-chat.svg) — 一次聊天（含工具审批）
- [`diagrams/sequence-memory-update.svg`](diagrams/sequence-memory-update.svg) — 记忆学习与画像投影
- [`diagrams/er-diagram.svg`](diagrams/er-diagram.svg) — 持久化关系
- [`assets/diagrams/runtime-overview.svg`](assets/diagrams/runtime-overview.svg) — 运行时拓扑
- [`assets/diagrams/backend-modules.svg`](assets/diagrams/backend-modules.svg) — 后端模块图

## 11. 已知限制

- 打包以 Windows 为先；其他平台仅用于开发。
- 工具沙箱是策略级，不是操作系统级隔离。
- 记忆检索目前是词法匹配；embedding 是明确的后续工作。
- 记忆规划器（定时整合/遗忘）已设计但尚未启用；配置会被保存，但界面明确标注未接入。
