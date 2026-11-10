# Documentation

Slothy documentation is bilingual: every public guide exists in English and
简体中文. Start with the root [README](../README.md) for the overview.

## Guides

| Document | English | 中文 | What it covers |
|---|---|---|---|
| Architecture | [architecture.md](architecture.md) | [architecture.zh-CN.md](architecture.zh-CN.md) | processes, layers, agent graph, decision layer, memory pipeline, prompt layering, storage, security |
| Getting started | [getting-started.md](getting-started.md) | [getting-started.zh-CN.md](getting-started.zh-CN.md) | install, configure a provider, run, test, package, troubleshoot |
| API reference | [api.md](api.md) | [api.zh-CN.md](api.zh-CN.md) | all `/api/v1` routes, SSE events, approval flow, examples |
| Brand guidelines | [brand.md](brand.md) | [brand.md#中文](brand.md#中文) | name, slogan, colors, logo usage, voice, UI principles |
| Changelog | [CHANGELOG.md](../CHANGELOG.md) | — | release history |
| Release notes | [0.1.4.2-beta](releases/0.1.4.2-beta.md) | same file, 中文 after English | bilingual release details |

## Diagrams

Rendered SVG plus Mermaid sources live in [`diagrams/`](diagrams):

| File | Content |
|---|---|
| `architecture-overall.svg` | process and layer overview |
| `sequence-chat.svg` | one chat turn, including tool review |
| `sequence-memory-update.svg` | memory learning and profile projection |
| `er-diagram.svg` | persistence relationships |

Two additional high-resolution diagrams are under
[`assets/diagrams/`](assets/diagrams): the runtime topology and the backend
module map.

## Conventions

- English docs are the default files (`*.md`); Chinese versions use the
  `*.zh-CN.md` suffix and are linked from each other.
- Code identifiers, paths and commands stay in English in both languages.
- Every claim about behaviour should be traceable to source code or tests in
  `core/` and `web/`.
- Brand assets are documented once in [brand.md](brand.md); other docs link to
  it instead of redefining colors.
