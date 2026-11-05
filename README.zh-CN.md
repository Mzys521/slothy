<p align="center">
  <img src="docs/assets/brand/mascot.png" width="120" alt="Slothy 思洛吉祥物：抱着一片叶子的微笑树懒" />
</p>

<h1 align="center">Slothy · 思洛</h1>

<p align="center"><em>小步迭代，迈向更好未来</em></p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-0.1.3-60DD06" />
  <img alt="license" src="https://img.shields.io/badge/license-MIT-95611F" />
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-3776AB" />
  <img alt="electron" src="https://img.shields.io/badge/electron-45-47848F" />
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows-FCEAC9" />
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="docs/architecture.zh-CN.md">架构</a> ·
  <a href="docs/getting-started.zh-CN.md">快速开始</a> ·
  <a href="docs/api.zh-CN.md">API</a> ·
  <a href="docs/brand.md#中文">品牌</a>
</p>

---

**Slothy（思洛）** 是一个桌面端的本地优先自适应个人智能体。它由 Electron 原生外壳和运行在
本机的 FastAPI + LangGraph 服务组成：会话、长期记忆、用户画像和产物都保存在本地 SQLite，
不依赖云端账号。

它像一位细心的助理那样学习：从已完成的对话中抽取偏好、习惯与目标，请你确认后才写入版本化的
用户画像；每轮只检索与当前请求相关的记忆。会改动你机器的工具调用不会被静默执行，而是先暂停、
由你审批后再恢复同一次运行。

## 为什么是 Slothy

| | |
|---|---|
| **本地优先** | 会话、记忆、画像、产物都在本地 SQLite；桌面 API 只监听 `127.0.0.1`，每次启动生成访问令牌且渲染进程拿不到。 |
| **可审计的智能体** | 每次运行逐步落检查点；工具调用可暂停、查看参数、批准或拒绝，然后从同一断点恢复。 |
| **有出处的记忆** | 一条记忆 = claim + 证据 + 作用域。候选必须经你确认，画像版本保留历史，删除会同步擦除旧快照中的原文。 |
| **真正的桌面产品** | 无边框窗口、原生目录选择、工作区映射到真实文件夹、NSIS 安装包、离线数据目录、DPAPI 加密的模型密钥。 |
| **可维护的工程** | 严格分层、确定性的提示词组装、221 个自动化测试、生产资源审查，以及中英双语的架构与 API 文档。 |

## 能力

- **智能体运行时**：LangGraph `StateGraph`，节点为
  `prepare_run → load_profile → retrieve_memories → build_personalized_context →
  agent →（tools）→ finalize_response`，同步检查点、有界递归步数。
- **人在回路工具审批**：高风险工具调用通过 `interrupt()` 暂停运行，批准/拒绝后以
  `Command(resume=…)` 恢复。
- **自适应记忆**：对已完成轮次做规则抽取 → 人工审核 → 冲突感知的确认 → 按作用域检索
  （global / project / thread），并提供记忆关系图谱。
- **版本化用户画像**：偏好、习惯、目标投影为不可变的历史版本；下一轮注入当前版本及来源记忆 ID。
- **分层提示词与缓存**：L1 稳定系统规则 + 会话冻结画像快照，L2 只追加历史（周期性摘要），
  L3 易变的当轮参考放在尾部；稳定前缀在连续请求间保持字节一致，服务端前缀缓存可持续命中。
- **内置工具**：计算器、金融计算、受限命令、隔离 Python 进程生成文档、读取上传文件。
- **工作区即目录**：每个工作区映射真实文件夹，工具实例与产物目录按目录隔离，不改变进程工作目录。
- **模型接入**：DeepSeek、MiMo、Anthropic 以及任意 OpenAI 兼容服务；密钥用 Windows DPAPI 加密，
  API 只返回掩码状态。

## 架构一览

```
┌──────────────────────────── Electron 桌面端 ──────────────────────────────┐
│ 主进程                          渲染进程（Vue 3 + TypeScript）            │
│ 窗口 / IPC / 令牌               聊天 · 记忆 · 画像 · 图谱 · 设置          │
│ 启动并守护后端                  访问 http://127.0.0.1:<port>/api/v1      │
└──────────────┬───────────────────────────────────────────────────────────┘
               │ 仅回环地址，携带 X-Slothy-Token
┌──────────────▼───────────────────────────────────────────────────────────┐
│ 本地 FastAPI 服务（uvicorn）                                              │
│  /api/v1 路由 ──▶ AgentRunner ──▶ LangGraph online_agent                  │
│                                  ├── ▶ 工具审批 / 恢复                    │
│                                  └── ▶ 记忆 · 画像 · 工作区              │
│  SQLite：检查点 · 会话归档 · 记忆与画像                                   │
└───────────────────────────────────────────────────────────────────────────┘
```

模块地图、图拓扑、提示词分层、数据库结构与安全边界见
[架构文档](docs/architecture.zh-CN.md)；渲染好的图在 [`docs/diagrams/`](docs/diagrams/)。

## 快速开始

环境要求：**Windows 10/11 x64**、Python **3.11+**、Node.js **20+**、npm。

```powershell
# 1. 克隆
git clone https://github.com/Mzys521/slothy.git
cd slothy

# 2. 后端环境
cd core
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example ..\.env      # 然后编辑 ../.env，选择模型服务

# 3. 桌面端
cd ..\web
npm install
npm run dev
```

`npm run dev` 会启动 Vite 与 Electron；Electron 主进程从 `core/.venv` 拉起本地后端、注入本次
启动的令牌并打开窗口。首次运行请在 **设置 → API** 配置模型服务。

安装包构建、环境变量、故障排查见 [快速开始](docs/getting-started.zh-CN.md)。

## 目录结构

```text
core/                  Python 后端（FastAPI + LangGraph）
  app/api.py           HTTP 路由、SSE、生命周期装配
  app/agent/           图、工具、Provider、提示词规划
  app/personalization/ 记忆抽取与画像投影
  app/infrastructure/  SQLite 存储、LLM 适配、子进程执行
  tests/               221 个自动化测试
web/                   Electron + Vue 桌面客户端
  electron/            主进程、preload 桥、后端守护
  src/                 Vue 页面与 TypeScript API 层
docs/                  公共文档（中英）与图表
```

## 质量

```powershell
cd core
.\.venv\Scripts\python.exe -m pytest      # 221 个测试
.\.venv\Scripts\python.exe -m ruff check app tests

cd ..\web
npm run typecheck                         # vue-tsc
npm run production:audit                  # 打包资源白名单审查
```

## 安全与隐私

- API 只监听 `127.0.0.1`，没有启动令牌的请求一律拒绝；令牌只存在于主进程与后端。
- 模型密钥使用 Windows DPAPI 加密，API 永不返回明文。
- 工具执行采用显式命令白名单、argv 形式、最小环境与输出上限；文档代码在独立 Python 进程中运行，
  并施加路径与导入策略。
- 对话推理只会发送到**你自己配置**的模型端点；Slothy 自身没有云端后端和账号体系。

## 路线图

- [ ] 记忆规划图：定时整合、摘要、衰减与遗忘、自动画像投影（设计稿整理中）。
- [ ] 更丰富的记忆检索（可选 embedding、混合排序）。
- [ ] 更多 Provider 与界面中的提示词缓存遥测。

## 文档

| 文档 | English | 中文 |
|---|---|---|
| 架构 | [architecture.md](docs/architecture.md) | [architecture.zh-CN.md](docs/architecture.zh-CN.md) |
| 快速开始 | [getting-started.md](docs/getting-started.md) | [getting-started.zh-CN.md](docs/getting-started.zh-CN.md) |
| API 参考 | [api.md](docs/api.md) | [api.zh-CN.md](docs/api.zh-CN.md) |
| 品牌规范 | [brand.md](docs/brand.md) | [brand.md](docs/brand.md#中文) |
| 更新日志 | [CHANGELOG.md](CHANGELOG.md) | [CHANGELOG.md](CHANGELOG.md) |

## 许可证

MIT，见 [LICENSE](LICENSE)。© 2026 Slothy contributors。

<p align="center">
  <img src="docs/assets/brand/wordmark-light.png" width="220" alt="Slothy 思洛 字标" />
  <br />
  <sub>
    <span style="color:#60DD06">绿色负责行动</span> ·
    <span style="color:#FCEAC9">奶油色负责温度</span> ·
    <span style="color:#95611F">棕色负责品牌</span>
  </sub>
</p>
