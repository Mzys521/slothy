<p align="center">
  <img src="docs/assets/brand/mascot.png" width="120" alt="Slothy mascot — a smiling sloth holding a leaf" />
</p>

<h1 align="center">Slothy · 思洛</h1>

<p align="center"><em>Small Steps Toward A Better Future · 小步迭代，迈向更好未来</em></p>

<p align="center">
  <img alt="version" src="https://img.shields.io/badge/version-0.1.3-60DD06" />
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

---

**Slothy** is a local-first adaptive personal agent for the desktop. It pairs a
native Electron shell with a FastAPI + LangGraph service running on your own
machine, so conversations, long-term memory, user profile and artifacts stay in
local SQLite files instead of a cloud account.

The agent learns the way a careful assistant would: it extracts preferences,
habits and goals from finished conversations, asks before promoting them into a
versioned user profile, and retrieves only the memories relevant to the current
request. Tool calls that change your machine are reviewed by you, not trusted
blindly.

## Why Slothy

| | |
|---|---|
| **Local-first by design** | Sessions, memory, profiles and artifacts live in local SQLite; the desktop API binds to `127.0.0.1` with a per-launch token that never reaches the renderer. |
| **An agent you can audit** | Every run is checkpointed step by step. You can pause on a tool call, inspect the exact arguments, approve or reject it, and resume the same run. |
| **Memory with provenance** | A memory is a claim plus evidence plus a scope. Nothing is silently learned: candidates are reviewed, profile versions keep history, and deletions redact old snapshots. |
| **A real desktop product** | Frameless window, native file dialogs, workspace folders on disk, NSIS installer, offline data directory, DPAPI-encrypted provider keys. |
| **Built to be maintained** | Strict layering, deterministic prompt assembly, 221 automated tests, production asset audit, and design notes kept next to the code. |

## Features

- **Agent runtime** — LangGraph `StateGraph` with the nodes
  `prepare_run → load_profile → retrieve_memories → build_personalized_context →
  agent → (tools) → finalize_response`, synchronous checkpointing and bounded
  recursion.
- **Human-in-the-loop tools** — risky tool calls pause the graph through an
  `interrupt()`; approve or reject, then resume with `Command(resume=…)`.
- **Adaptive memory** — rule-based candidate extraction from finished turns,
  human review, conflict-aware confirmation, scoped retrieval
  (global / project / thread) and a relationship graph.
- **Versioned user profile** — preferences, habits and goals are projected into
  an immutable profile history; the next turn injects the current version with
  source memory IDs.
- **Layered prompt & cache** — L1 stable system rules + session-frozen profile
  snapshot, L2 append-only history with periodic summaries, L3 the volatile
  per-turn references at the tail. Stable prefixes stay byte-identical across
  turns for provider prefix caching.
- **Built-in tools** — calculator, financial calculator, constrained shell
  commands, document generation in an isolated Python worker, and reading of
  uploaded files.
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
window. On first run, open **Settings → API** and configure a model provider
(DeepSeek, MiMo, Anthropic or any OpenAI-compatible endpoint).

Install and package details, environment variables and troubleshooting live in
[Getting started](docs/getting-started.md).

## Project layout

```text
core/                  Python backend (FastAPI + LangGraph)
  app/api.py           HTTP routers, SSE, lifespan wiring
  app/agent/           graph, tools, providers, prompt planning
  app/personalization/ memory extraction, profile projection
  app/infrastructure/  SQLite stores, LLM adapters, subprocess runner
  tests/               221 automated tests
web/                   Electron + Vue desktop client
  electron/            main process, preload bridge, backend supervision
  src/                 Vue pages and TypeScript API layer
docs/                  public documentation (EN + 中文) and diagrams
```

## Quality

```powershell
cd core
.\.venv\Scripts\python.exe -m pytest      # 221 tests
.\.venv\Scripts\python.exe -m ruff check app tests

cd ..\web
npm run typecheck                         # vue-tsc
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

- [ ] Memory planner graph: scheduled consolidation, summarization, decay and
  forgetting, automatic profile projection (design notes in progress).
- [ ] Richer memory retrieval (optional embeddings, hybrid ranking).
- [ ] More providers and prompt-cache telemetry in the UI.

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
