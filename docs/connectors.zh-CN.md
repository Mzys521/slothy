# Slothy 连接器

在「连接器」中选择「本地工具」「MCP」或「Skills」。每页的添加和导入入口位于列表上方，格式与权限说明可在页底展开。

## 配置与管理

- **MCP** 支持 stdio、Streamable HTTP 和 SSE。stdio 可逐项填写启动参数、工作目录和环境变量，HTTP 与 SSE 可填写请求头及超时。环境变量和请求头的已存值在编辑时保持隐藏，可保留、替换或移除。
- **连接测试** 在确认后进行真实的 MCP 握手与工具发现。结果列出发现的工具和失败原因，HTTP 端点拒绝时显示状态码。测试可以在保存前或保存后进行。使用「刷新工具列表」才能用刚发现的定义替换已保存的工具清单。
- **Skills** 支持用 Markdown 编写、预览和编辑指令。可一次选择多个参考文件，或移除待上传和已保存的文件。保存时服务端生成 SKILL.md、YAML Front Matter 和参考文件目录。已有 ZIP 技能可以直接编辑。
- **本地工具** 展示已有注册项的描述、权限等级、启用状态和参数定义。可启停内置工具，并继续导入 JSON 清单和 ZIP 工具包。

## 接口与持久化

连接器仍存于现有注册表和包目录。新表单和旧导入共享记录格式及启用规则。Skill 编辑先准备替换目录，再提交记录。待审批的 MCP 调用带有工具标识，保存编辑后的配置会更新这个标识。需要启动进程或联系服务的管理操作有独立确认，Agent 调用工具继续由 Runtime 审批。配置和连接错误的上游原始响应不写入工作日志。

新增连接器详情读取、MCP 与 Skill 表单创建和更新、MCP 草稿与已保存配置测试、MCP 工具刷新。原二进制导入、启停和删除接口继续使用。列表增加可选的版本、连接状态和参考文件数量字段。[中文接口文档](api.zh-CN.md#9-连接器) · [English API reference](api.md#9-connectors)

## 测试

`vue-tsc --noEmit` 与 Ruff 检查通过。后端全量 568 项通过，连接器和工具注册表定向复验 42 项通过，前端与桌面逻辑 103 项通过。

| 验收范围 | 实测内容 |
| --- | --- |
| 实际 MCP 协议 | 用独立 stdio 进程与本地 HTTP/SSE MCP 服务验证握手、发现、请求头、CWD、超时字段和 HTTP 401。失败保留已有配置与工具定义。 |
| 审批 | 取消或缺少确认时不启动本地进程。已启用的 MCP 在工作模式调用前进入审批。修改连接后，用旧审批无法执行更改后的工具。 |
| Skill 存储与加载 | 核对生成的 YAML、Markdown 和文件，更新并读取参考文件，确认启停影响 Runtime 注册。重启服务后恢复已保存配置。 |
| 导入兼容与错误 | 保留旧 JSON 和 ZIP 导入。验证重复配置、旧版本编辑、非法文件名、超限 ZIP、链接和路径穿越处理。 |
| Vue 页面 | 用 Vite 生产构建连接真实本地后端，验证表单、动态字段、真实成功和失败反馈、权限与参数、编辑、启停、导入和删除确认。检查深色与浅色及窄窗口。 |

## 当前限制

- MCP 按需建立连接。「已连接」表示当前进程最近一次探测成功。重启后，已启用的服务先显示「待验证」。
- Skill 可保存随附文件。当前 Runtime 的参考读取器仅支持不超过 1 MiB 的 UTF-8 文本。
- stdio 与本地工具使用用户配置的现成程序和依赖。导入与 Skill 保存只写配置和文件。

## 修改文件

| 文件 | 改动 |
| --- | --- |
| [core/app/connectors/api.py](../core/app/connectors/api.py) | 增加详情、创建、编辑、测试与刷新契约，处理确认、输入限制和诊断。 |
| [core/app/connectors/registry.py](../core/app/connectors/registry.py) | 复用注册、持久化和真实 MCP 客户端，跟踪探测状态并更新调用标识。 |
| [core/app/connectors/bundles.py](../core/app/connectors/bundles.py) | 共享 ZIP 解包和参考文件路径校验。 |
| [core/app/connectors/skills.py](../core/app/connectors/skills.py) | 读取与生成 YAML，校验参考文件并准备新的技能包目录。 |
| [core/pyproject.toml](../core/pyproject.toml) | 将环境已有的 PyYAML 声明为直接依赖。 |
| [core/tests/integration/test_connectors.py](../core/tests/integration/test_connectors.py) | 增加三种协议、配置修订、审批、重启恢复和技能文件安全测试。 |
| [web/src/ConnectorsPage.vue](../web/src/ConnectorsPage.vue) | 组织三类页面和列表操作，保留原导入入口。 |
| [web/src/appearance.css](../web/src/appearance.css) | 移除旧连接器列表样式。 |
| [web/src/connectors/connectors.css](../web/src/connectors/connectors.css) | 使用主题变量设置卡片、表单、窄栏和可滚动对话框。 |
| [web/src/connectors/types.ts](../web/src/connectors/types.ts) | 定义接口类型与双语状态和错误文字。 |
| [web/src/connectors/McpEditor.vue](../web/src/connectors/McpEditor.vue) | 提供按传输方式显示字段的创建和编辑表单。 |
| [web/src/connectors/SkillEditor.vue](../web/src/connectors/SkillEditor.vue) | 提供 Markdown 预览、参考文件和技能配置编辑。 |
| [web/src/connectors/SecretFields.vue](../web/src/connectors/SecretFields.vue) | 提供隐藏值的键值表单及新增、修改、移除交互。 |
| [web/src/connectors/ConnectorCard.vue](../web/src/connectors/ConnectorCard.vue) | 展示状态与操作，按需加载服务、工具和技能详情。 |
| [web/src/connectors/ToolDetails.vue](../web/src/connectors/ToolDetails.vue) | 展示工具描述、参数及可展开的完整定义。 |
| [web/src/connectors/ConnectorDialog.vue](../web/src/connectors/ConnectorDialog.vue) | 复用原生对话框处理焦点、关闭及忙碌状态。 |
| [web/src/connectors/ConfirmConnection.vue](../web/src/connectors/ConfirmConnection.vue) | 对运行程序或联系 MCP 服务提示并征得确认。 |
| [web/src/connectors/McpProbeResult.vue](../web/src/connectors/McpProbeResult.vue) | 展示握手结果、工具数量、名称及失败诊断。 |
| [docs/api.md](api.md#9-connectors) | 更新英文接口文档。 |
| [docs/api.zh-CN.md](api.zh-CN.md#9-连接器) | 更新中文接口文档。 |
| 本文 | 汇总操作、验收、限制与逐文件改动。 |
