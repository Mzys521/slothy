# Brand guidelines

Slothy is a calm, deliberate assistant. The brand should feel like a slow,
capable animal: not sleepy, but unhurried and trustworthy. This page documents
the visual and verbal identity so docs, the app and future materials stay
consistent.

## 1. Name

| | |
|---|---|
| English name | **Slothy** |
| Chinese name | **思洛** |
| Product phrase | Slothy · 思洛 |
| Mascot | a smiling sloth holding a green leaf |

Use `Slothy` in running text and `Slothy · 思洛` when both languages appear
together (for example in a page title or installer).

## 2. Slogan

> **Small Steps Toward A Better Future.**
>
> **小步迭代，迈向更好未来。**

Use the slogan as a subtitle or footer, not as a headline competing with the
product name.

## 3. Color system

The three brand colors are permanent and shared by all Slothy materials.

| Token | Name | HEX | Role |
|---|---|---|---|
| `slothy.green` | Slothy Green | `#60DD06` | action, growth, success, primary accents |
| `slothy.cream` | Slothy Cream | `#FCEAC9` | warmth, surfaces, soft highlights |
| `slothy.brown` | Slothy Brown | `#95611F` | identity, headings, mascot-adjacent tones |

Principle: **green is action · cream is warmth · brown is identity · neutral is
information.**

Practical rules:

- Use green for interactive emphasis only; never as long body text color.
- Cream is a background/surface color, not a text color on light backgrounds.
- Brown carries identity: mark, headings, borders of brand blocks.
- Neutral greys carry 90% of UI text and chrome; the brand colors should be
  accents, not walls of color.

## 4. Logo and wordmark

| Asset | Path | Use |
|---|---|---|
| Mascot | `docs/assets/brand/mascot.png` | README hero, about page, installer art |
| Wordmark (light surface) | `docs/assets/brand/wordmark-light.png` | light backgrounds, print, docs |
| Wordmark (dark surface) | `docs/assets/brand/wordmark-dark.png` | dark backgrounds, splash, dark UI |

Rules:

- Keep clear space around the mark equal to at least the height of the leaf.
- Minimum wordmark width: 160 px on screen, 40 mm in print.
- Do not recolor the mascot, stretch the wordmark, add shadows/outlines, rotate
  the mark, or place the black wordmark on a dark background.
- In Markdown, prefer the `<picture>` element so GitHub switches wordmarks with
  the reader's color scheme.

```html
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/wordmark-dark.png">
    <img alt="Slothy 思洛" src="docs/assets/brand/wordmark-light.png" width="320">
  </picture>
</p>
```

## 5. Voice and tone

- **Calm and precise.** Short sentences; concrete verbs; no hype.
- **Honest about limits.** Say what is planned, what is policy-level and what is
  not yet enabled. Never claim a capability that the code does not have.
- **Second person, sparingly.** Address the user as “you”/“你”, not as a persona.
- **Bilingual by default.** Chinese and English both count as first-class; keep
  term pairs stable (`用户画像 / user profile`, `记忆 / memory`,
  `智能体 / agent`).

Examples:

| Prefer | Avoid |
|---|---|
| “Approve the tool call to continue.” | “Awesome! Let’s do this!” |
| “Memory is stored locally in SQLite.” | “Your data is 100% unhackable.” |
| “Planned: memory consolidation.” | “Coming soon: perfect memory.” |

## 6. UI principles

1. **Quiet surfaces, clear actions.** Cream and neutrals for surfaces; green for
   the single primary action on a screen.
2. **Approval is explicit.** Tool review shows the exact call, the workspace and
   the consequences before the user decides.
3. **Motion is functional.** Transitions explain state changes; no celebratory
   animations, no confetti, no auto-playing loops.
4. **Local-first trust cues.** Show where data lives and what leaves the machine
   (only the configured model endpoint).
5. **Bilingual UI text stays paired.** Keep both languages in the translation
   map, and test layouts in both.

## 7. Assets and permissions

The MIT license covers the source code. Brand assets are © 2026 Slothy
contributors and may be used to refer to the project (docs, forks, articles)
with a link back; do not use them to imply endorsement or to brand unrelated
products without permission.

---

## 中文

### 名称与口号

- 英文名：**Slothy**；中文名：**思洛**；组合写法：**Slothy · 思洛**
- 口号：**小步迭代，迈向更好未来**（英文：Small Steps Toward A Better Future.）
- 吉祥物：抱着一片绿叶、微笑的树懒

### 色彩系统

| 令牌 | 名称 | 色值 | 角色 |
|---|---|---|---|
| `slothy.green` | 思洛绿 | `#60DD06` | 行动、成长、成功、主要强调色 |
| `slothy.cream` | 思洛奶油 | `#FCEAC9` | 温度、表面、柔和高光 |
| `slothy.brown` | 思洛棕 | `#95611F` | 品牌身份、标题、品牌区块边框 |

原则：**绿色负责行动，奶油色负责温度，棕色负责品牌，黑白灰负责信息。**

- 绿色只用于交互强调，不做正文颜色。
- 奶油色用于背景与表面，不作为浅色背景上的文字色。
- 棕色承担身份感：字标、标题、品牌区块。
- 中性灰承担 90% 的界面文字与框架，品牌色只做点睛。

### 字标与图形

| 资产 | 路径 | 用途 |
|---|---|---|
| 吉祥物 | `docs/assets/brand/mascot.png` | README 主视觉、关于页、安装图 |
| 字标（浅色背景） | `docs/assets/brand/wordmark-light.png` | 浅色界面、文档、印刷 |
| 字标（深色背景） | `docs/assets/brand/wordmark-dark.png` | 深色界面、启动页 |

规则：留白不小于叶片高度；屏幕最小宽度 160 px；不得改色、拉伸、加阴影描边，不得把黑色字标
放在深色背景上。Markdown 中建议用 `<picture>` 让 GitHub 随主题切换字标。

### 语调

- **平静、准确**：短句、具体动词，不吹嘘。
- **诚实**：明确区分“已实现 / 策略级 / 尚未启用”，不声称代码没有的能力。
- **双语并列**：中英文均为一等公民，术语保持一致（用户画像 / user profile，
  记忆 / memory，智能体 / agent）。

### 界面原则

1. 表面安静、动作清晰：奶油色与中性灰做表面，绿色只用于当屏唯一主操作。
2. 审批必须显式：展示完整调用、工作区与后果，再让用户决定。
3. 动效服务于状态解释：没有庆祝动画、彩带或自动循环。
4. 本地优先信任线索：说明数据在哪、什么离开本机（只有用户配置的模型端点）。
5. 双语界面成对维护，两种语言都要测布局。

### 资产许可

源代码采用 MIT 协议。品牌资产版权归 © 2026 Slothy contributors 所有，可在注明指向本项目
链接的前提下用于介绍本项目（文档、分支、文章）；未经许可不得用于暗示背书或为无关产品背书。
