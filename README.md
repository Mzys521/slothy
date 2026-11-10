<p align="center">
  <img src="docs/assets/brand/mascot.png" width="120" alt="Slothy mascot — a smiling sloth holding a leaf" />
</p>

<h1 align="center">Slothy · 思洛</h1>

<p align="center"><em>Small Steps Toward A Better Future · 小步迭代，迈向更好未来</em></p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-0.1.4.2--beta-60DD06" />
  <img alt="license" src="https://img.shields.io/badge/license-MIT-95611F" />
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-3776AB" />
  <img alt="electron" src="https://img.shields.io/badge/electron-45-47848F" />
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows-FCEAC9" />
</p>

<p align="center">
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="docs/architecture.md">Architecture</a> ·
  <a href="docs/getting-started.md">Getting started</a> ·
  <a href="docs/api.md">API</a> ·
  <a href="docs/brand.md">Brand</a>
</p>

<p align="center">
  <a href="https://github.com/Mzys521/slothy/releases/tag/v0.1.4.2-beta"><strong>Download Slothy 0.1.4.2 Beta for Windows x64</strong></a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

---

**Slothy** is a local-first adaptive personal agent for the desktop. It pairs a
native Electron shell with a FastAPI + LangGraph service running on your own
machine, so conversations, long-term memory, user profile and artifacts stay in
local SQLite files instead of a cloud account.

An independent background LLM evaluates completed turns for preferences, habits,
goals, facts and episodes. Reliable, evidence-backed information is saved automatically
and projected into a versioned profile; uncertain or sensitive candidates remain
reviewable. Fully manual review is also available. Each request retrieves relevant
memory. Tool calls that change your machine are reviewed by you, not trusted
blindly.

## Memory V3 — 0.1.4.2-beta

The background model extracts typed concepts and evidence-backed relations. Python
and beginner are separate concepts; proficiency belongs to Python, not to the user
as a generic label. Concepts and facts have independent, session-deduplicated
frequencies and strength `f/(f+4)`. SQLite FTS5, aliases and one-hop relation recall
need no embedding service. Manual profile edits and automatic learning share the
same facts; corrections retire old versions, and identity questions use reliable
facts directly. The context panel shows what was actually provided and why.

This development release resets all three business databases **once on first V3
startup**, without migrating/relearning old conversations. Provider keys, connector
configuration files, UI preferences and workspace files remain. Old browser session
caches are cleared when the data epoch changes. No TTL or time decay is applied.
The published GitHub release now carries this `0.1.4.2-beta` build; building this
checkout reproduces the same version locally.

## Why Slothy

| | |
|---|---|
| **Local-first by design** | Sessions, memory, profiles and artifacts live in local SQLite; the desktop API binds to `127.0.0.1` with a per-launch token that never reaches the renderer. |
| **An agent you can audit** | Every run is checkpointed step by step. You can pause on a tool call, inspect the exact arguments, approve or reject it, and resume the same run. |
| **Memory with provenance** | A memory is a claim plus evidence plus a scope. Reliable information is saved automatically; you can review, edit, archive or delete it. Deletion redacts references and blocks resurrection during historical reanalysis. |
| **A real desktop product** | Frameless window, native file dialogs, workspace folders on disk, NSIS installer, offline data directory, DPAPI-encrypted provider keys. |
| **Built to be maintained** | Strict layering, deterministic prompt assembly, full backend/frontend regression suites, production asset audit, and bilingual architecture/API documentation. |

## Features

- **Agent runtime** — LangGraph `StateGraph` with the nodes
  `prepare_run → load_profile → retrieve_memories → build_personalized_context →
  agent → (tools) → finalize_response`, synchronous checkpointing and bounded
  recursion.
- **Human-in-the-loop tools** — risky tool calls pause the graph through an
  `interrupt()`; approve or reject, then resume with `Command(resume=…)`.
- **Reasoning trace** — when the provider returns reasoning text it streams in a
  separate collapsible block above the answer, auto-collapses once reasoning finishes,
  and is archived with the turn; it is display-only and never injected into prompts or
  message content.
- **Decision model in API settings** — pick any configured provider/model to drive routing,
  tool selection and context planning; leave it off for chat-model self-driving, with
  automatic handoff to the chat model if a decision call fails.
- **Opt-in model catalog** — provider discovery only lists models; nothing is imported
  automatically. Users search, multi-select, and run a tiny real API test per model before
  adding; failures stay selectable with their reason, and existing configured models are
  never overwritten by a refresh.
- **Observability** — rotating application logs and per-session JSONL model-call records
  capture prompt/context composition, tool schemas, provider usage, cache hits, duration,
  status and redacted parameters. The right-side Work Details tab renders horizontal context bars
  and call hierarchy; About Slothy opens the managed log directory.
- **DeepSeek web search** — the `deepseek_search` tool calls DeepSeek's official
  `POST /v1/tools/search` API (title, URL, summaries, content, source, publication time).
  Configure an independent Search key in Settings → API; otherwise the DeepSeek model
  key is reused. With neither key the tool is not registered, so ordinary chat is unaffected.
- **Native MiMo web search** — supported MiMo v2.6 models receive the provider's own
  `web_search` tool by default; if the account plugin is disabled the request transparently
  retries without search.
- **Reviewed high-risk tools** — risky calls pause immediately for the runtime approval
  dialog; the model never asks for confirmation in prose. Denials are returned as
  `USER_DENIED` tool results, recorded as rejected in audit/activity, and cannot be
  retried or bypassed in the same run.
- **Drag-and-drop attachments** — drop TXT, Markdown, PDF, DOCX, JSON, CSV and similar
  documents onto the conversation; validation reports count/size/type errors without
  breaking the turn, and PDF text is extracted with a bounded parser.
- **Reviewed deletion** — `workspace_delete` is a Chat/Work high-risk tool: paths are
  normalized and confined to the workspace, symlinks/roots/private paths are rejected,
  recursive deletion requires explicit approval, and the target is revalidated at
  execution time.
- **Clear local data** — About → Local data removes conversations, memory, profiles,
  indexes, databases, checkpoints, caches, logs and local configuration after an exact
  `确认删除` confirmation; failures and residue are reported instead of a false success.
- **Model-driven decisions** — an optional dedicated decision model proposes the
  route, tool subset and context budget; deterministic guards clamp it, `off` /
  `shadow` / `enforce` control rollout, and rules are the fallback. The plan never
  bypasses approval and stays out of the prompt cache.
- **Composer commands and references** — `/` and the plus button share a menu for
  20 commands and file upload; `@` selects workspace files/folders, conversations
  and plugins. Compression/export require confirmation, references remain bounded
  L3 data, and plugin execution keeps its existing approval policy.
- **First-run onboarding** — a centered, theme-aware glass overlay guides the
  first launch: pick a provider template (DeepSeek, MiMo, OpenAI, Anthropic, Kimi
  or Qwen) or connect a custom endpoint, then drop into the workspace. It only
  appears while no provider is configured; failures offer retry instead of
  pretending the app is unconfigured.
- **Connectors** — import local tools (JSON/ZIP), MCP servers (stdio, Streamable
  HTTP or legacy SSE) and Skills (ZIP with `SKILL.md`). Enabling discovers tools
  through the real connection; imported tools require approval, and disabling
  revokes execution immediately.
- **Message previews & copy** — SVG/HTML previews fill the conversation width with
  content-driven height and follow the live theme; per-message copy uses the
  native clipboard bridge with browser fallback.
- **Appearance** — Slothy green by default, a persistent custom accent with HEX
  validation and readable light/dark contrast, plus a language switch on the
  welcome screen.
- **Long-term memory V3** — the background model extracts typed concepts and
  evidence-backed directed facts; per-session frequency dedupes and strengthens them
  (`f/(f+4)`). SQLite FTS5, aliases and one-hop relation recall need no embedding
  service, identity questions answer from reliable facts, and manual edits share the
  same fact service as autonomous learning.
- **Versioned profile & facts** — concepts, facts, preferences, habits and goals
  project into an immutable profile history; explicit corrections supersede old
  versions, deletion removes evidence and historical profile bodies, and the next
  turn injects only the scoped current version with source IDs.
- **Layered prompt & cache** — L1 stable system rules + session-frozen profile
  snapshot, L2 append-only history with periodic summaries, L3 the volatile
  per-turn references at the tail. Stable prefixes stay byte-identical across
  turns for provider prefix caching.
- **Built-in tools** — calculation, paged workspace reads and literal search,
  reviewed file writes, template export (md/txt/csv/json/docx), timezone lookup,
  reviewed Python execution and uploaded-file reads. Work has 10 tools; Chat has 8
  (the optional DeepSeek search tool is added only after a Search key is resolved).
  Results, artifacts, risks and per-tool measurements use one contract;
  journaled writes can be recovered without repeating completed effects.
- **Optional HTTPS retrieval** — disabled by default; requires an explicit domain
  allowlist and rejects non-public addresses and unsafe redirects.
- **Workspaces on disk** — each workspace maps to a real folder; tool instances
  and artifact roots are isolated per directory, without changing the process
  working directory.
- **Model providers** — DeepSeek, MiMo, Anthropic and any OpenAI-compatible
  endpoint; keys are stored with Windows DPAPI and only masked state is exposed
  to the API.

## Architecture at a glance

```
┌──────────────────────────── Electron desktop ────────────────────────────┐
│ main process                     renderer (Vue 3 + TypeScript)           │
│  window / IPC / token            chat · memory · profile · graph · settings│
│  spawns & supervises backend     fetches http://127.0.0.1:<port>/api/v1  │
└──────────────┬───────────────────────────────────────────────────────────┘
               │ X-Slothy-Token (loopback only)
┌──────────────▼───────────────────────────────────────────────────────────┐
│ FastAPI service (uvicorn, 127.0.0.1)                                      │
│  /api/v1 routers ──▶ AgentRunner ──▶ LangGraph online_agent               │
│                                  ├── ▶ tool review / resume               │
│                                  └── ▶ memory · profile · workspaces     │
│  SQLite: checkpoints · sessions · workspace memory                        │
└───────────────────────────────────────────────────────────────────────────┘
```

See [Architecture](docs/architecture.md) for the module map, graph topology,
prompt layering, storage schema and security boundaries. The rendered diagrams
are in [`docs/diagrams/`](docs/diagrams/).

## Quick start

Requirements: **Windows 10/11 x64**, Python **3.11+**, Node.js **20+**, npm.

```powershell
# 1. clone
git clone https://github.com/Mzys521/slothy.git
cd slothy

# 2. backend environment
cd core
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example ..\.env      # then edit ../.env and pick a provider

# 3. desktop app
cd ..\web
npm install
npm run dev
```

`npm run dev` starts Vite and Electron; the Electron main process launches the
local backend from `core/.venv`, injects the per-launch token and opens the
window. On first run the setup overlay appears while no provider is configured —
pick a template or a custom connection (DeepSeek, MiMo, OpenAI, Anthropic, Kimi,
Qwen or any OpenAI-compatible endpoint), save the key, and the workspace opens.
Later changes live under **Settings → API**.

Install and package details, environment variables and troubleshooting live in
[Getting started](docs/getting-started.md).

## Project layout

```text
core/                  Python backend (FastAPI + LangGraph)
  app/api.py           HTTP routers, SSE, lifespan wiring
  app/agent/           graph, tools, providers, prompt planning
  app/personalization/ memory extraction, profile projection
  app/infrastructure/  SQLite stores, LLM adapters, subprocess runner
  tests/               424 backend automated tests
web/                   Electron + Vue desktop client
  electron/            main process, preload bridge, backend supervision
  src/                 Vue pages and TypeScript API layer
docs/                  public documentation (EN + 中文) and diagrams
```

## Quality

```powershell
cd core
.\.venv\Scripts\python.exe -m pytest      # 424 tests
.\.venv\Scripts\python.exe -m ruff check app tests

cd ..\web
npm run typecheck                         # vue-tsc
node --test tests/*.test.mjs              # 43 frontend/serverless tests (+ 3 desktop directory tests)
npm run production:audit                  # packaged-asset allowlist audit
```

## Security & privacy

- The API listens on `127.0.0.1` only and rejects requests without the
  per-launch token; the renderer never receives that token.
- Provider keys are encrypted with Windows DPAPI and never returned by the API.
- The tool runner uses an explicit command allowlist, argv-only execution, a
  minimal environment and bounded output; generated documents run in a separate
  Python process with path and import policies.
- Chat inference is sent only to the model endpoint **you** configure. Slothy
  itself has no cloud backend and no account system.

## Roadmap

- [ ] Scheduled memory consolidation with decay and retention enforcement (settings
  are reserved today; automatic permanent deletion is not enabled).
- [ ] Richer memory retrieval (optional embeddings, hybrid ranking).
- [ ] Conversation branching (documented plan; not implemented yet).
- [ ] More providers and richer cache analytics in the UI.

## Documentation

| Document | English | 中文 |
|---|---|---|
| Architecture | [architecture.md](docs/architecture.md) | [architecture.zh-CN.md](docs/architecture.zh-CN.md) |
| Getting started | [getting-started.md](docs/getting-started.md) | [getting-started.zh-CN.md](docs/getting-started.zh-CN.md) |
| API reference | [api.md](docs/api.md) | [api.zh-CN.md](docs/api.zh-CN.md) |
| Brand guidelines | [brand.md](docs/brand.md) | [brand.md](docs/brand.md#中文) |
| Changelog | [CHANGELOG.md](CHANGELOG.md) | [CHANGELOG.md](CHANGELOG.md) |

## License

MIT — see [LICENSE](LICENSE). © 2026 Slothy contributors.

<p align="center">
  <img src="docs/assets/brand/wordmark-light.png" width="220" alt="Slothy 思洛 wordmark" />
  <br />
  <sub>
    <span style="color:#60DD06">Green is action</span> ·
    <span style="color:#FCEAC9">Cream is warmth</span> ·
    <span style="color:#95611F">Brown is identity</span>
  </sub>
</p>

