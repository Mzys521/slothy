# Architecture

Slothy is a local-first desktop agent. Its architecture is shaped by four
constraints: **the model runs remotely but the user's data stays local**,
**every run must be resumable**, **tool effects need human approval**, and
**prompt prefixes must stay stable** so provider caching works.

## 1. Design principles

1. **Local-first** — the desktop app talks to a backend bound to `127.0.0.1`;
   sessions, memory, profiles and artifacts are local SQLite/files.
2. **Explicit boundaries** — HTTP layer, application orchestration, agent graph,
   persistence, model adapters and tool execution are separate modules with
   one-way dependencies.
3. **Deterministic replay** — LangGraph checkpoints persist every super-step;
   approvals and failures resume from the same state instead of starting over.
4. **Human-in-the-loop for side effects** — non-safe tools pause the run.
5. **Memory is evidence-based** — a memory is a claim with provenance, scope and
   review history; the profile is a versioned projection, not a hidden blob.
6. **Stable prefixes, volatile tails** — prompt assembly is ordered by change
   frequency (see §6) so caching is a property of the layout, not luck.

## 2. Runtime topology

```
Electron main process                 Electron renderer (Vue 3)
├─ BrowserWindow, frameless UI        ├─ chat / memory / profile / graph pages
├─ preload bridge (contextBridge)     ├─ fetch /api/v1 + SSE parsing
├─ per-launch token (crypto random)   └─ never sees the token
├─ spawns + supervises backend
└─ exits backend when the app quits
              │ loopback HTTP (127.0.0.1:<random port>)
┌─────────────▼──────────────────────────────────────────────────────────────┐
│ uvicorn + FastAPI                                                          │
│  middleware: token check → Origin check → request id                       │
│  routers ──▶ AgentRunner ──▶ LangGraph online_agent                        │
│  services: providers, conversations, workspace store, memory evolution     │
│  infrastructure: checkpoints (SQLite), LLM adapters, subprocess runner     │
└─────────────┬──────────────────────────────────────────────────────────────┘
              │
      %APPDATA%\slothy\data (installed)  /  core\data (dev)
      checkpoints.sqlite · checkpoints.sessions.sqlite ·
      checkpoints.workspace.sqlite · providers.json · artifacts/
```

Key properties:

- The backend port is OS-assigned (`bind(("127.0.0.1", 0))`) and announced to the
  main process over stdout as `{"event":"listening","port":N}`.
- A random 32-byte token is passed to the backend through the environment and
  injected into `/api/v1/*` requests by the Electron session handler. The
  renderer never receives it.
- The main process watches the backend process; if it exits unexpectedly the app
  reports an error and quits. In packaged builds the backend is a frozen
  executable (`resources/backend/slothy-service.exe`).
- The Python entry point (`core/desktop/entry.py`) also exits when the desktop
  process disappears, so no orphan backend survives.

## 3. Backend layering

| Layer | Modules | Responsibility | Must not |
|---|---|---|---|
| HTTP / API | `app/api.py` | routes, SSE framing, request models, lifespan wiring | contain agent logic |
| Application service | `app/services/agent.py` | run orchestration, per-thread locking, archive & resume | import SQLite specifics |
| Agent domain | `app/agent/` (`graph`, `prompting`, `tools`, `providers`) | graph topology, nodes, prompt plan, tool schemas | open databases or read env |
| Memory domain | `app/personalization/` | claim extraction, review, profile projection | know HTTP or the graph |
| Infrastructure | `app/infrastructure/` (`checkpoints`, `conversations`, `workspace`, `llm`, `processes`) | SQLite stores, session archive, model adapters, bounded subprocesses | import graph nodes |
| Tool logic | `app/tools/` | calculation, commands, document worker, attachment reading | import LangChain |
| Composition root | `web/electron/*`, `core/desktop/entry.py` | process lifecycle, configuration, packaging | business rules |

Dependency direction is one-way: HTTP → application → domain → infrastructure.
The graph is compiled once per `(mode, model, effort, workspace directory)` and
cached; tools are bound to the model at compile time.

## 4. Agent graph

```
START → prepare_run → load_profile → retrieve_memories
      → build_personalized_context → agent ─┬─ no tool calls → finalize_response → END
                                            └─ tool calls ──▶ tools ──▶ agent
                                                             │
                                        needs review ──▶ review_tools (interrupt)
                                                             ├─ approve → tools
                                                             └─ reject  → agent
```

- **prepare_run** validates the new request, resets per-run fields and bounds
  working history by complete user turns.
- **load_profile / retrieve_memories / build_personalized_context** are read-only
  ports: profile snapshot, scoped memory candidates, and a budgeted reference
  text. Recoverable failures become warnings, not fake answers.
- **agent** assembles the prompt through the prompt planner (§6) and calls the
  model with bound tools.
- **tools** executes the whole tool-call batch; **review_tools** pauses the graph
  with `interrupt()` when the batch contains a non-safe call. Approval resumes
  with `Command(resume={approval_id: true})`; rejection returns typed error
  `ToolMessage`s to the agent, so the model can recover.
- **finalize_response** only succeeds with a non-empty assistant message and no
  pending tool calls.

Execution properties:

- Checkpointer: `AsyncSqliteSaver` (WAL, `synchronous=FULL`), one snapshot per
  super-step, `durability="sync"`.
- Thread identity for storage is `[user_id, thread_id]`; the business IDs remain
  in state.
- The runner serializes runs per thread with an in-process lock and repairs a
  missing archive entry for the previous turn before advancing.
- A per-request approval policy (`ask` | `every`) decides which calls pause;
  safe tools and read-only commands never pause under `ask`.

## 5. Memory and user profile

```
finished turn ──▶ archive (sessions.sqlite) ──▶ learning queue
                                                │
                       rule-based claim extraction (preferences/habits/goals/facts/episodes)
                                                ▼
                              memory_claims + memory_evidence (workspace.sqlite)
                                                │
                          review (user confirm/edit/ignore) ──▶ confirmed memory
                                                │
                        profile projection (preference/habit/goal) ──▶ profile version +N
                                                │
                       next turn: scoped retrieval + profile snapshot injection
```

- Claims have a scope (`global` / `project` / `thread`), a dimension, a stable
  topic key where possible, and evidence quotes.
- The profile is an immutable version chain; edits use optimistic concurrency
  (`expected_version`), deletions redact old snapshots, and only content changes
  create a new version (last 200 kept).
- Memory relationships (`shared_conversation`, manual links) are stored in the
  same workspace database; the graph API supports directed/undirected links and
  deletion.
- Workspaces map to real directories; tool instances and artifact roots are
  isolated per resolved directory without changing the process working directory.
  This is a path policy, not an OS sandbox.

## 6. Prompt layering and caching

Requests are assembled as three layers, ordered by change frequency:

| Layer | Content | Change rate | Cache strategy |
|---|---|---|---|
| **L1** | Versioned system instructions, sorted tool instructions/schemas, session-frozen profile snapshot | Per session | Stable prefix; explicit breakpoints on Anthropic |
| **L2** | Conversation history (append-only), periodic summary | Append / occasional compression | Prefix grows turn by turn |
| **L3** | Per-turn retrieved memories, profile delta, attachment references, current user message | Every turn | Tail; never cached |

Rules that follow from the design:

- Volatile references are **after** history, never before it — otherwise every
  turn invalidates the whole prefix.
- History is compressed in batches (threshold + batch size) instead of trimming
  one turn per request; compression is an explicit cache-reset event with a
  versioned summary.
- The profile snapshot is frozen when a session starts; later learning reaches
  the current session through the L3 delta, and a new session picks up the newest
  snapshot.
- Provider-reported cache tokens are recorded in run metrics together with segment
  hashes and sizes, so hit-rate changes are measurable.

## 7. Persistence

| File | Owns | Representative tables |
|---|---|---|
| `checkpoints.sqlite` | LangGraph execution state | checkpoints / writes |
| `checkpoints.sessions.sqlite` | conversation archive, threads, preferences, accounts, attachments, approved memory configuration | conversation_turns / threads / preferences / local_accounts / conversation_files |
| `checkpoints.workspace.sqlite` | memory, claims, evidence, profiles, relationships, prompt snapshots, retrieval cache | workspace_memories / memory_claims / memory_evidence / workspace_profiles / workspace_memory_links |
| `providers.json` | provider configuration | DPAPI-encrypted secrets, model catalog |

All stores run in WAL mode with foreign keys enabled where relevant; schema
upgrades are idempotent `ALTER TABLE` migrations executed on open.

## 8. Tools and execution boundaries

| Tool | Execution | Boundary |
|---|---|---|
| `calculate` / `calculate_financial` | in-process | float / Decimal arithmetic only |
| `run_command` | in-process for allowlisted commands, subprocess for `rg` | enum commands, argv list, workspace-confined paths, no shell |
| `write_document_python` | separate Python process | fresh run directory, audit policy, artifact budgets |
| `list_uploaded_files` / `read_uploaded_file` | in-process SQLite reads | per-thread isolation, paged reads |

Subprocesses use `create_subprocess_exec` (argv only), a minimal environment
allowlist, merged stderr, output caps and timeout kills. Document generation is
policy-checked but **not** an OS sandbox; the security boundary is documented
rather than implied.

## 9. Security model

| Concern | Control |
|---|---|
| Local API access | loopback bind, per-launch token, Host and Origin checks |
| Renderer compromise | `contextIsolation`, `sandbox`, no `nodeIntegration`, strict IPC sender validation |
| Provider secrets | Windows DPAPI, masked API responses, no logging |
| Tool effects | approval gate for non-safe tools, allowlists, path checks, output budgets |
| Data retention | explicit delete endpoints, redaction of historical profiles, per-memory deletion |
| Inference privacy | requests go only to the model endpoint the user configured |

## 10. Diagrams

- [`docs/diagrams/architecture-overall.svg`](diagrams/architecture-overall.svg) — process and layer overview
- [`docs/diagrams/sequence-chat.svg`](diagrams/sequence-chat.svg) — one chat turn, including tool review
- [`docs/diagrams/sequence-memory-update.svg`](diagrams/sequence-memory-update.svg) — memory learning and profile projection
- [`docs/diagrams/er-diagram.svg`](diagrams/er-diagram.svg) — persistence relationships
- [`docs/assets/diagrams/runtime-overview.svg`](assets/diagrams/runtime-overview.svg) — runtime topology in detail
- [`docs/assets/diagrams/backend-modules.svg`](assets/diagrams/backend-modules.svg) — backend module map

## 11. Known limits

- Packaging is Windows-first; other platforms are development-only.
- The tool sandbox is policy-based, not an OS-level isolation boundary.
- Memory retrieval is lexical today; embeddings are an explicit future step.
- The memory planner (scheduled consolidation / forgetting) is designed but not
  yet enabled; configuration is stored but marked inactive in the UI.
