# 快速开始

本指南带你从全新克隆走到可运行的桌面应用，再到打包安装包。

## 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Windows | 10 / 11 x64 | 主要支持平台（打包以 Windows 为先） |
| Python | 3.11+ | 后端与工具链 |
| Node.js | 20+（含 npm） | 桌面外壳与渲染层 |
| Git | 较新版本 | 克隆与更新 |

磁盘约需 2 GB（依赖与构建产物）。应用数据目录本身很小。

## 1. 克隆仓库

```powershell
git clone https://github.com/Mzys521/slothy.git
cd slothy
```

## 2. 配置 Python 后端

```powershell
cd core
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

可选：只有需要生成 Excel / PDF / PPTX 文档时才安装：

```powershell
python -m pip install -e ".[documents]"
```

从模板生成本地环境文件：

```powershell
Copy-Item .env.example ..\.env
```

也支持 `core/.env`（覆盖 `../.env`）。进程环境变量优先级最高。`.env` 已被 git 忽略，绝不能提交。

### 选择模型服务

有两种方式，任选其一：

1. **在应用内配置（推荐）**：首次启动且尚未配置服务商时，会出现居中的引导层。选择内置模板
   （DeepSeek、MiMo、OpenAI、Anthropic、Kimi、Qwen）或自定义 OpenAI/Anthropic 兼容连接，
   填入密钥并保存，应用会直接进入工作区。之后可在 **设置 → API** 中修改。密钥用 Windows DPAPI
   加密后保存在数据目录的 `providers.json`。可在此另存/测试/删除独立的 **DeepSeek Search
   API Key**；未配置时复用 DeepSeek 模型密钥，两者都没有时不暴露 `deepseek_search`。
   高风险工具调用会暂停并弹出审批，不会用普通聊天消息替代确认。
2. **在 `.env` 中配置**：适合后端 CLI 与示例：

```ini
# .env — 只使用所选服务组的变量
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat
# DEEPSEEK_API_URL=https://api.deepseek.com

# MiMo
# LLM_PROVIDER=mimo
# MIMO_API_KEY=...
# MIMO_MODEL=...

# 任意 OpenAI 兼容服务 / 代理
# LLM_PROVIDER=openai_compatible
# OPENAI_API_KEY=...
# OPENAI_API_BASE=https://api.openai.com/v1
# OPENAI_MODEL=...
```

`LLM_MODEL`、`LLM_API_KEY`、`LLM_BASE_URL` 是可选通用覆盖项。模型名必须显式提供，Slothy
不会自动切换模型。

可选决策模型：在 **设置 → API → 决策模型** 中选择已配置模型，密钥与地址复用所属 Provider。
未选择时由聊天模型自主决策；决策模型不可用、输出非法或超预算时，控制权交回聊天模型。
决策计划不会绕过工具审批，也不会进入提示词缓存文本。仅后端运行仍可使用 `DASHSCOPE__*` /
`DECISION_*` 环境变量。

## 3. 安装桌面端依赖

```powershell
cd ..\web
npm install
```

## 4. 开发运行

```powershell
npm run dev
```

运行过程：

1. Vite 在 `127.0.0.1:5173` 启动渲染层开发服务器。
2. Electron 以开发 URL 启动（主进程：`web/electron/main.cjs`）。
3. 主进程从 `core/.venv/Scripts/python.exe core/desktop/entry.py` 拉起后端，使用随机回环端口
   和每次启动生成的访问令牌。
4. 渲染层访问 `http://127.0.0.1:<port>/api/v1`，主进程为 `/api/v1/*` 请求注入令牌。

首次运行检查清单：

- **首次配置**：仅在尚未配置服务商时出现引导层。选择模板或自定义连接，填入 API Key 保存后，
  应用自动进入工作区，不整页刷新。
- **新建会话**：发一条消息，确认回复以流式返回。
- **工具审批**：请求生成文档或执行命令时，运行会暂停并展示完整调用参数，批准后继续。
- **记忆 V3**：陈述偏好、习惯、目标或事实后，后台模型会提炼语义概念与有证据的有向事实；
  身份问答直接使用可靠事实，上下文面板展示实际提供的证据。首次启动 V3 会一次性重置三份业务
  数据库（不会重新学习旧消息）——升级前请先导出需要保留的数据。

### 输入区命令与引用

在输入框输入 `/` 打开 20 个分组可搜索的命令（会话、模型、上下文、产物与帮助）；文件上传已并入
同一个加号菜单。输入 `@` 可引用工作区文件夹或文件、当前用户拥有的会话，或已启用的插件；服务端
会重新校验归属与作用域，再注入有界低信任 L3 数据。`/compact` 与 `/export` 需要确认，插件工具
调用仍遵循既有审批策略。也可以把文件直接拖入对话：TXT、Markdown、PDF、DOCX、JSON、CSV 等
文档会先校验（最多 8 个、单个 2 MiB）并进入待发送附件；PDF 文本在发送前本地提取。

## 5. 数据位置

| 模式 | 数据目录 |
|---|---|
| 开发 | `core/data/`（检查点、会话归档、工作区记忆、产物） |
| 安装版 | `%APPDATA%\slothy\data\` |

两者都包含 `checkpoints.sqlite`、`checkpoints.sessions.sqlite`、
`checkpoints.workspace.sqlite`、`providers.json` 与 `artifacts/`。**记忆 V3**：首次启动
V3 会一次性重置三份业务数据库；模型密钥、连接器配置文件、界面偏好与工作区实际文件保留。删除目录即可重置应用；
备份目录即可保留记忆。

## 6. 构建 Windows 安装包

```powershell
cd ..\web
python -m pip install "pyinstaller>=6"    # build:backend 需要，未包含在 [dev] 中
npm run build
```

构建分四步：渲染层构建（`vite build` + `vue-tsc`）、后端冻结（PyInstaller，
`core/desktop/service.spec`）、发布准备、NSIS 打包（`electron-builder`）。
安装包输出到 `web/release/slothy/`。

常用子命令：

```powershell
npm run build:web        # 仅渲染层
npm run build:backend    # 仅冻结后端运行时
npm run build:dir        # 解包目录，不生成安装包
```

## 7. 测试与检查

```powershell
# 后端
cd core
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check app tests

# 桌面端
cd ..\web
npm run typecheck
npm run production:audit
```

说明：少数测试会启动真实子进程并创建命名管道，需要正常的 Windows 会话；在受限沙箱中会以
`WinError 5` 失败。

## 8. 常见问题

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 窗口打开但所有请求失败 | 后端未启动或令牌不一致 | 查看终端后端输出；确认 `core/.venv` 与依赖已安装。 |
| `Cannot find module 'electron'` | 未在 `web/` 执行安装 | `cd web; npm install`。 |
| `MODEL_UNAVAILABLE` / 503 | 未配置服务或模型不在目录 | 在设置 → API 配置，或检查 `.env` 中选中的服务组。 |
| 聊天正常但工具不执行 | 调用被拒绝或等待审批 | 打开审批卡片或 `GET /threads/{id}/approval`。 |
| 编辑画像报 `VERSION_CONFLICT` | 画像已更新 | 重新加载画像页后重试。 |
| `build:backend` 失败 | venv 缺少 PyInstaller | `python -m pip install "pyinstaller>=6"`。 |
| 测试报 `WinError 5` | 沙箱终端禁止命名管道 | 在普通终端运行测试。 |

## 下一步

- [架构](architecture.zh-CN.md) — 各模块如何协作。
- [API 参考](api.zh-CN.md) — 路由与 SSE 事件。
- [品牌规范](brand.md#中文) — 颜色、字标使用与语调。
