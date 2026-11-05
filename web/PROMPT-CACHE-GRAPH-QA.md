# 0.1.3 缓存分层与类型聚集验收

日期：2026-11-05。沿用 0.1.3，交付安装包信息见 [发行验证](RELEASE-VERIFICATION.md)。

## 实现

- 图谱按类型及节点 ID 生成稳定邻域，同类节点弱聚集；真实边的牵引强于类别偏好，跨类型关系保持可见。没有添加虚构的关系或修改记忆类型。
- 保留 0.6px 普通边、1px 关联高亮边及非缩放线宽；选中使用铜色圆环，无关内容变暗但可见。暂停/恢复、拖拽、刷新保位、减少动态继续有效。
- 消息按稳定 L1、历史 L2、动态 L3 排列。L1 保存会话冻结的全局画像；学习和编辑通过尾部参考生效。删除、归档、遗忘、移除偏好和禁用个性化优先撤销旧内容，允许必要的缓存失效。
- 历史超过 24 轮时整批压缩 8 轮，摘要与覆盖范围保存在检查点。使用主模型或指定摘要模型，失败回退至有界摘录；模型已报告的摘要 token 计入总消费。完整归档与工具审批不受影响。
- LRU + SQLite 检索缓存使用 TTL、容量和用户/线程/项目/查询/索引/画像版本隔离；后台学习写入同样触发失效。Anthropic 仅在稳定层设置显式断点，OpenAI 兼容协议不加该字段。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 完整后端回归 | 221 passed |
| 缓存专项复核 | 13 passed，含 100 轮、画像更新/分叉/关闭/清空、SQLite 双连接失效、TTL/LRU、主模型摘要与用量、SDK 序列化与 thinking 签名保持 |
| 前端数据与物理模拟 | 12 passed，含同类凝聚、跨类型边牵引、250 节点边界、帧率与暂停时间、Token 图表 |
| Ruff、Vue / TypeScript、Vite | 通过 |
| 真实本机页面 | 隔离资料库的 9 个节点、6 条真实关系；分类邻域、持续运动、暂停坐标不变、恢复及 Escape 取消选中通过；控制台无 error / warn |
| 圆形高亮 | 节点 outline=none；关联项 opacity=1，无关项 opacity=0.42，保留可见 |
| 桌面与最终冻结后端 | smoke、真实 SDK、上传、SSE、工具、审批、五种文档、分层消息、画像 delta 及重启冻结前缀全部通过 |
| 包内一致性 | 0.1.3；11 个 dist 文件和两个图标逐字节一致；生产审查 24 项资源、284 文件、6892 模块、0 禁止项 |

## 前缀实验

相同离线 20 轮输入，每轮检索和画像版本变化。以规范序列化请求的最长公共前缀字节数除以当前输入字节数，再计算第 10 轮及之后的均值：

| 布局 | 平均稳定前缀占比 |
| --- | --- |
| 修改前 | 16.37% |
| 分层后 | 91.62% |

原始数据为 `core/qa/prompt-cache/baseline.json` 和 `optimized.json`，探针为 `core/tests/support/prompt_cache_probe.py`。这不是实际供应商缓存计费比例；未使用个人密钥验证付费上游缓存、首 token 延迟或金标回答评分。`/runtime` 的实际命中统计只采信供应商返回的 usage，缺失时为未知。

官方协议依据：[Claude prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)、[DeepSeek context caching](https://api-docs.deepseek.com/guides/kv_cache/)。缓存还受供应商最小前缀、存续期和服务策略影响，不能仅凭布局保证一次请求命中。

## 界面证据

浏览器使用专用 `build/activity-graph-preview-profile`，未接入个人资料或外部模型，验收后关闭。

![类型邻域](qa/prompt-cache-graph/overview.jpg)

![铜色圆环与关联高亮](qa/prompt-cache-graph/selected.jpg)

![运行中的分类图谱](qa/prompt-cache-graph/running.jpg)

冻结后端、资源白名单、安装包校验结果以 [发行验证](RELEASE-VERIFICATION.md) 的同版本重发记录为准。
