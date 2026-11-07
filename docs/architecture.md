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
- The renderer keeps a first-run onboarding overlay above the shell. It is shown
  only while `GET /api/v1/settings/providers` reports `setup_required` (no runner
  configured) and closes after a provider is saved; configuration read failures
  show a retry instead of pretending the app is unconfigured.

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

## 5. Memory and user profile V3

`archive → durable learning queue → LLM concepts/facts → evidence and scope
validation → fact transaction → reinforcement → profile/search/graph projection`.
The online agent remains read-only. No embedding service is introduced.

- `app/memory/learning.py` retains background orchestration and strict bounded JSON
  (32 concepts, 16 facts). `MemoryService` reuses the existing transaction/review
  lifecycle while owning V3 writes; `MemoryStore` owns scoped local recall and graph
  projections. Provider calls never hold the write lock.
- Concepts contain typed canonical keys and supported aliases per user/scope.
  Facts own subject, predicate, target, value, evidence, state and version chain.
  A semantic edge is the same fact ID. A proficiency value must retain its skill
  target. Shared conversation alone never creates a semantic relationship.
- Concept and fact reinforcement use separate unique session events. Qualified
  evidence is distinct from reinforcement credit. Strength is independently
  `f/(f+c)`, default c=4; no collection normalization, TTL or time decay. Capacity
  (default 500 active facts) deduplicates, then archives unprotected low utility
  facts. Identity, explicit preferences and manual protection are preserved.
- Identity and personal skills are global; project statements retain their scope.
  Scoped FTS5, Chinese bigrams, English terms, aliases and identity intent feed
  one-hop recall, ranked `.75 relevance + .15 fact strength + .10 importance`.
  Unrelated high-frequency skills are excluded. Eight facts share a 1200-token
  personalization budget with their concept metadata.
- Facts are the only authority. Manual and automatic writes project immutable
  `identity/skill/preference/habit/goal` attributes; there is no writable
  profile_json/preferences branch. Optimistic profile edits create new fact
  versions, and deletion redacts evidence and all historical profile bodies.
- Direct self-identity questions bypass generation and use live global facts.
  Absence, pending learning/review, conflicts, disabled memory and read errors are
  explicit. Bounded memory_trace records supplied evidence IDs, relationship paths,
  scores and reasons, without copying fact text or claiming model adoption.
- Durable queue generations, configuration revisions, source exclusions and SQLite
  unique events prevent stale application and replay. Network failures and daily
  token reservations retain the existing observable worker lifecycle.

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

`ToolDescriptor → ToolRegistry → ToolGateway → ToolNode → executor` is the shared
assembly path for built-ins, attachments and connectors. Descriptors define schema,
mode/platform availability, review, static instructions and limits. Model bindings
exclude hidden aliases. Runtime names retain registration order; prompt guidance
and schemas remain sorted. Independent arithmetic/executor helpers retain native APIs.

Every model-visible result uses `ToolResult`: `ok`, `summary`, `data`, `artifacts`,
`next_offset`, `error`. Errors include a stable code, retryability and correction hint.
Default review covers write/execute/network; `every` covers every available valid
call. Skill references are read-only in both modes; imported executable connectors
are Work-only. Review cards expose risk, affected path and structured arguments.

Batches execute sequentially. Defaults are 12 calls, 64,000 output characters,
120 seconds of tool execution and three consecutive failures of one tool per run.
Budgets resume from checkpointed result telemetry; approval waiting is separate.
Bounded diagnostics still complete call/result pairs after budget exhaustion.
`RunMetrics.tools` reaches SSE, archives and daily/account tool aggregation.
`usage_reported=false` distinguishes absent model usage from reported zero tokens.

| Tool | Execution | Boundary |
|---|---|---|
| `calculate` / `calculate_financial` | in-process | float / Decimal string arithmetic |
| `workspace_read` | in-process | bounded UTF-8 pages, directory list and literal search; private/linked paths excluded |
| `workspace_write` | in-process | approved mkdir/write; no overwrite by default |
| `export_document` | standard library / python-docx | approved md/txt/csv/json/docx templates; no model code |
| `run_python` | separate Python process | approved code, journaled output directory, existing audit and artifact limits |
| `get_datetime` | in-process | current time and offset-aware IANA conversion; bundled tzdata |
| `list_uploaded_files` / `read_uploaded_file` | in-process SQLite reads | per-thread isolation and paged text |

File effects claim an invocation journal before executing. Completed results are
reused after checking paths, sizes and SHA-256 digests. Uncertain execution or changed
artifacts return `RECOVERY_REQUIRED` with a journal path. Retention defaults to 100
completed/failed managed run directories, preserving uncertain runs and workspace
business files. Files and SQLite remain separate transaction boundaries. Legacy
`run_command` / `write_document_python` aliases only recover pre-upgrade runs and stay
out of model bindings/runtime lists. Prompt version `2026-11-07.memory-v3` intentionally
resets the changed tool prefix once.

Subprocess execution keeps argv-only calls, minimal environments, output caps,
timeout termination and cancellation cleanup. Python audit checks are policy-level
constraints, not an OS sandbox.

Optional `web_fetch` is disabled by default and advertised only with an explicit
domain allowlist. It accepts HTTPS/443 without URL credentials, checks all DNS
results and every redirect, dials a validated public IP and verifies TLS against
the original hostname. Bytes, text and redirects are bounded; script/style content
is omitted. This setting is separate from imported MCP connection URLs.

## 9. Connectors

Connectors extend the tool surface without restarting the app.

| Kind | Import format | Execution model |
|---|---|---|
| Local tool | JSON or ZIP with `tool.json` | argv command, JSON stdin/stdout, workspace as cwd, `${plugin}` for the bundle directory |
| MCP server | `mcpServers` JSON (stdio `command,args,env`; HTTP `url,type,headers`) | real connection discovery (`streamable-http` or legacy `sse`); tool calls reviewed |
| Skill | ZIP with exactly one `SKILL.md` plus reference files | low-trust L3 instructions; `read_skill_file` pages UTF-8 references |

Imported items start disabled. Enabling discovers tools through the real
connection; failure preserves the previous state. Imported tools require approval
in both chat and work modes, disabling revokes execution immediately, and changed
definitions change their tool identifiers. Configuration, credentials, environment
variables and extracted paths are never returned by the API; Windows stores
connector configuration with DPAPI. Limits: 10 MiB import, 20 MiB / 512 extracted
entries, 64 imported items, 64 enabled imported tools, 8 enabled skills with 64,000
instruction characters combined. Interpreters and dependencies for local/MCP
commands must already be installed; importing or enabling never runs bundled
scripts.

## 10. Security model

| Concern | Control |
|---|---|
| Local API access | loopback bind, per-launch token, Host and Origin checks |
| Renderer compromise | `contextIsolation`, `sandbox`, no `nodeIntegration`, strict IPC sender validation |
| Provider secrets | Windows DPAPI, masked API responses, no logging |
| Tool effects | approval gate for non-safe tools, allowlists, path checks, output budgets |
| Data retention | explicit delete endpoints, redaction of historical profiles, per-memory deletion |
| Inference privacy | requests go only to the model endpoint the user configured |

## 11. Diagrams

- [`docs/diagrams/architecture-overall.svg`](diagrams/architecture-overall.svg) — process and layer overview
- [`docs/diagrams/sequence-chat.svg`](diagrams/sequence-chat.svg) — one chat turn, including tool review
- [`docs/diagrams/sequence-memory-update.svg`](diagrams/sequence-memory-update.svg) — memory learning and profile projection
- [`docs/diagrams/er-diagram.svg`](diagrams/er-diagram.svg) — persistence relationships
- [`docs/assets/diagrams/runtime-overview.svg`](assets/diagrams/runtime-overview.svg) — runtime topology in detail
- [`docs/assets/diagrams/backend-modules.svg`](assets/diagrams/backend-modules.svg) — backend module map

## 12. Known limits

- Packaging is Windows-first; the installer is unsigned; other platforms are
  development-only.
- Tool and connector execution is policy-based, not an OS sandbox; connector
  commands run with the user's privileges and require preinstalled interpreters
  and dependencies.
- The LLM memory-learning engine is enabled (`auto` / `review` / `shadow` /
  `off`), but scheduled consolidation, decay, TTL and permanent deletion remain
  reserved settings and are not executed automatically.
- Memory retrieval is lexical today; embeddings are an explicit future step.
- Conversation branching is documented but not implemented yet.

## V3 data epoch

Before opening stores, `prepare_storage` resets exactly the three business databases
under an exclusive lock and writes an atomic `.storage.json` marker. Independent
configuration files and actual workspace files remain. The marker precedes opening
new stores, so restart never deletes accepted V3 data. The renderer clears legacy
conversation caches on `/runtime.data_epoch` change and does not import old messages.
Every model call and approval resumption revalidates frozen fact IDs, states and
values; corrected/archived/deleted values leave L1 and new values enter L3.
