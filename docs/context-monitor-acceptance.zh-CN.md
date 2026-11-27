# 上下文监控侧栏交付记录

关联记忆现在按记忆 ID 展示卡片。工作详情默认汇总本轮调用，可以切换到单次调用。截图来自 Windows 上的 Chrome，运行生产构建，使用固定会话数据复现给定的 Token 构成。面板宽度均通过拖动应用内的分隔线设置。

环境上下文按工作区、会话、模型与记忆分卡。已装载工具按当前模式展示，一项工具对应一张卡片。

| 视图 | 面板宽 480px | 面板宽 320px |
| --- | --- | --- |
| 关联记忆 | [查看原图](assets/context-monitor/memory-480.png) | [查看原图](assets/context-monitor/memory-320.png) |
| 环境上下文 | [查看原图](assets/context-monitor/environment-480.png) | [查看原图](assets/context-monitor/environment-320.png) |
| 已装载工具 | [查看原图](assets/context-monitor/tools-480.png) | [查看原图](assets/context-monitor/tools-320.png) |
| 工作详情 | [查看原图](assets/context-monitor/work-480.png) | [查看原图](assets/context-monitor/work-320.png) |

## 环境上下文与已装载工具

工作区名称和目录来自当前工作区。会话卡片展示标题、运行状态和模式，有工作轮次审批记录时再展示审批策略。模型优先取最近一轮的指标，缺失时取运行时模型。定制记忆沿用全局开关与轮次设置，画像版本取最近一轮记录，版本 0 也会展示。

工具名称来自同一次 `GET /runtime` 响应中的 `chat_tools` 或 `work_tools`。展示时按原顺序去重，再用已有的 `tool_groups` 查找操作类别。该分组来自工作模式注册表，聊天模式只匹配其自身名单里的名称。分组缺失时显示「未分类」。卡片展示的是模式工具清单，本轮实际调用记录仍可在工作详情查看。[运行时接口](../core/app/api.py) · [注册表分组](../core/app/agent/tools/registry.py)

| 验收项 | 状态 | 结果 |
| --- | --- | --- |
| 环境字段分组 | 已实测 | 工作会话展示三张卡片。聊天模式隐藏工作审批，无会话时只展示工作区。 |
| 目录与可选字段 | 已实测 | 目录缺失显示短横线并隐藏复制按钮。覆盖画像版本为 0、记忆关闭、会话无标题等情况。 |
| 工具来源、数量与分类 | 已实测 | 同一工具名只出现一次。工作模式样例共 10 项，切换到聊天模式后仅有 2 项。旧响应缺少分组时仍保留工具名。 |
| 加载、失败与空状态 | 已实测 | 等待读取、运行时不可用、已就绪但清单为空各有对应状态卡。 |
| 480px 与 320px 布局 | 已实测 | 四个 Tab 保持单行。环境字段从横排变为纵排，工具卡片从双列变为单列。 |
| 长名称与目录 | 已实测 | 目录可换行，在 260px 面板中验证了长连接器工具名完整展示且无横向溢出。 |
| 键盘与复制 | 已实测 | 方向键换 Tab，Tab 聚焦复制按钮，Enter 触发反馈。浏览器测试核对了传给剪贴板的完整目录和工具标识。 |
| 主题与语言 | 已实测 | 深色示例沿用现有主题，开启减少动态效果后卡片无过渡。中英文标签均已在浏览器检查。 |
| 工程边界 | 已实现 | 本轮只更新展示组件、相关样式、测试和交付材料，复用既有接口、store、依赖和主题变量。 |
| 类型与定向测试 | 已实测 | `vue-tsc --noEmit` 通过。运行 runtimeCards 与 clipboard 两组共 8 项测试，全部通过。 |

### 环境与工具改动文件

| 文件 | 用途 |
| --- | --- |
| [AgentContextPanel.vue](../web/src/AgentContextPanel.vue) | 把已有环境字段、当前模式工具名单和运行时分组传给新卡片视图。 |
| [EnvironmentContext.vue](../web/src/monitor/EnvironmentContext.vue) | 组合三类环境卡片，支持长路径换行与复制。 |
| [LoadedTools.vue](../web/src/monitor/LoadedTools.vue) | 显示按模式计数的工具卡片、操作类别、复制入口及状态卡。 |
| [style.css](../web/src/style.css) | 移除旧环境表格和工具纯文本列表的样式。 |
| [runtimeCards.test.mjs](../web/tests/runtimeCards.test.mjs) | 渲染真实 Vue 组件，验证条件字段、工具去重与分类、旧数据和空状态。 |
| [environment-480.png](assets/context-monitor/environment-480.png) | 展示标准宽度的三张环境卡片。 |
| [environment-320.png](assets/context-monitor/environment-320.png) | 展示窄栏的纵排环境字段与目录换行。 |
| [tools-480.png](assets/context-monitor/tools-480.png) | 展示工作模式工具的双列卡片。 |
| [tools-320.png](assets/context-monitor/tools-320.png) | 展示同一工具清单的单列卡片。 |
| 本文档 | 记录字段来源、截图和本轮逐项验收结果。 |

## 数据核对

**记忆身份。** 旧 hash 行来自 `memory_trace.facts`，记录召回时的 ID、命中方式、是否纳入和可用的强度。旧 chip 的标题与下方正文来自 `used_memory_ids` 对应的记忆详情。画像属性也通过 `memory_id` 指向记忆，因此「当前画像属性」是命中方式，「偏好记忆」等是条目类型。界面按完整 ID 合并，保留短 ID 供辨认，点击复制完整 ID。强度优先使用本轮快照，缺失时使用详情值。概览只统计纳入项。因预算被排除的候选项单列，缺少详情时仅展示已有诊断。直接从图谱进入某条记忆时，只展示指定卡片。[召回诊断来源](../core/app/memory/context.py)

**估算与实际。** 旧界面的两个数字都取自选中的同一次调用。文本按字符数除以四粗估，工具定义按工具名称的估算值乘八计入。实际输入取自 Provider 对完整请求的用量回报，包含其上报的缓存命中输入。上述口径可以产生数量级差距，现有日志无法将样例中的差额精确归因到每一类。[估算公式](../core/app/agent/prompting/plan.py) · [调用分类与 Provider 用量记录](../core/app/observability/journal.py)

**本轮范围。** 工作日志接口返回会话记录，界面按运行 ID 选出本轮调用。两个卡片共享同一选择范围，汇总缓存命中率按缓存命中数之和除以实际输入之和计算。部分上报的用量以 ≥ 标记，未知值显示为 —。历史轮次的调用另行折叠。日志沿用打开侧栏或切换会话时的读取时机，在侧栏持续打开期间完成新调用，可重新打开侧栏取得新记录。

## 逐项验收

| 关联记忆 | 状态 | 验证结果 |
| --- | --- | --- |
| 480px 和 320px 的四个 Tab | 已实测 | 四项等宽同排。窄栏采用图标和悬停、键盘焦点提示，使四项同时可见。 |
| 概览提示 | 已实测 | 两种宽度下，提示末行完整可见，未被其他元素遮挡。 |
| 一条记忆只显示一次 | 已实测 | 四个完整 ID 对应四张卡片，概览为本地文本匹配 1 条、当前画像属性 3 条。 |
| 正文、类型、命中方式、强度条、短 ID | 已实测 | 两种宽度均已截图。长正文可展开，再收起到两行。 |
| 复制、原始证据、图谱跳转 | 已实测 | 读取剪贴板核对完整 ID，核对「已复制」提示，展开和收起证据，并在图谱页核对选中节点。 |
| 无召回 | 已实测 | 空会话样例显示一张空状态卡片。 |
| 键盘操作 | 已实测 | 方向键、Home 和 End 切换 Tab，Tab 进入控件，Enter 或空格可操作复制、折叠和跳转。收起的证据不进入焦点顺序。 |

| 工作详情 | 状态 | 验证结果 |
| --- | --- | --- |
| 示例占比和圆心总数 | 已实测 | 1483、280、131、124、5 按降序显示为 73%、14%、6%、6%、<1%，圆心为 2023。 |
| 极小正值 | 已实测 | 当前输入 5 保留至少 2° 的可见弧，图例为 <1%，分配出的角度从最大扇区扣除。 |
| 零值类别 | 已实测 | runtime 和 other 均为零时，图例和扇区各保留五项。 |
| 扇区、图例、圆心联动 | 已实测 | 实际移动鼠标到扇区及图例行，核对双方高亮和中心数值，再移出恢复 2023。 |
| 本轮汇总与单次调用 | 已实测 | 测试数据中两次估算为 987 和 1036，汇总为 2023。较早轮次的日志未计入，只有一次调用时无切换控件。 |
| 估算和 Provider 用量 | 已实测 | 独立卡片展示估算 2023 和实报输入 23822、输出 377、缓存命中 23296、耗时 3200 ms。命中率为 97.8%。 |
| 窄面板布局 | 已实测 | 480px 和 360px 左图右图例，359px 和 320px 上图下图例，内容区无横向溢出。 |
| 减少动态效果 | 已实测 | 普通模式有 400 ms 绘制动画。启用减少动态效果后，绘制、扇区悬停和证据折叠过渡均关闭。 |
| 调用明细与 JSON | 已实测 | 可以用键盘展开调用、复制三类 ID、展开上下文并将格式化 JSON 复制到系统剪贴板。 |

| 工程 | 状态 | 验证结果 |
| --- | --- | --- |
| TypeScript | 已实测 | `npm run typecheck` 即 `vue-tsc --noEmit` 通过。新增组件与展示辅助函数均有类型，未新增 `any`。 |
| 依赖与主题 | 已实现 | 使用原生 SVG。现有 D3 模块用于选择和缩放，项目没有现成的环形图库。沿用现有强调色与表面色。 |
| 数据与请求边界 | 已实现 | 本次变更限于前端展示、测试和交付材料。使用原有 props、store 字段、接口和读取逻辑。 |
| 测试范围 | 已实测 | 仅运行 contextMonitor、readGuard、clipboard 三组定向测试，12 项通过，并对实际渲染的侧栏做了浏览器交互验证。原条形图无快照测试。 |

原条形图七个硬编码颜色已原样迁入主题变量 `--chart-system`、`--chart-runtime`、`--chart-tool-schema`、`--chart-history`、`--chart-user`、`--chart-memory` 和 `--chart-other`。新用户的默认面板宽度为 480px，已保存的宽度优先。

## 关联记忆与工作详情改动文件

| 文件 | 用途 |
| --- | --- |
| [AgentContextPanel.vue](../web/src/AgentContextPanel.vue) | 组合新视图，传入原有召回数据与本轮运行 ID。 |
| [ContextMonitorTabs.vue](../web/src/monitor/ContextMonitorTabs.vue) | 实现四等分 Tab、窄栏图标提示和方向键导航。 |
| [RelatedMemories.vue](../web/src/monitor/RelatedMemories.vue) | 展示概览、来源分布、纳入与排除分组及空状态。 |
| [memoryCards.ts](../web/src/monitor/memoryCards.ts) | 按完整 ID 关联诊断和记忆详情，集中维护命中方式名称。 |
| [MemoryCard.vue](../web/src/monitor/MemoryCard.vue) | 展示正文、强度、确认会话、证据折叠和图谱入口。 |
| [CopyValue.vue](../web/src/monitor/CopyValue.vue) | 复用项目剪贴板能力，提供可聚焦的复制按钮和结果提示。 |
| [WorkDetails.vue](../web/src/WorkDetails.vue) | 显示范围切换、估算图、独立的 Provider 用量与调用列表。 |
| [workMetrics.ts](../web/src/monitor/workMetrics.ts) | 集中类别与颜色映射，计算各范围的估算、弧长和实际用量。 |
| [TokenDonutChart.vue](../web/src/monitor/TokenDonutChart.vue) | 用原生 SVG 绘制环形图，并同步扇区、图例及圆心。 |
| [ModelCallCard.vue](../web/src/monitor/ModelCallCard.vue) | 用折叠卡片展示模型、状态、耗时、可复制 ID 及原始 JSON。 |
| [ChatPage.vue](../web/src/ChatPage.vue) | 将用户点击的记忆 ID 转交给应用导航。 |
| [App.vue](../web/src/App.vue) | 将卡片事件接入已有的图谱定位函数。 |
| [style.css](../web/src/style.css) | 增加七个原图表色变量，更新面板布局、卡片、焦点和减少动态效果样式。 |
| [contextMonitor.test.mjs](../web/tests/contextMonitor.test.mjs) | 覆盖记忆去重、缺失数据、按调用汇总、极小扇区及部分上报。 |
| [memory-480.png](assets/context-monitor/memory-480.png) | 展示标准宽度下的记忆概览与四张卡片。 |
| [memory-320.png](assets/context-monitor/memory-320.png) | 展示窄栏图标 Tab、正文折行与卡片操作。 |
| [work-480.png](assets/context-monitor/work-480.png) | 展示环形图与图例并排时的实际布局。 |
| [work-320.png](assets/context-monitor/work-320.png) | 展示环形图与图例上下排列时的实际布局。 |
| 本文档 | 汇总数据核对结论、实测结果、截图及逐文件改动。 |
