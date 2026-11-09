# Changelog

All notable changes to Slothy are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
semantic versioning for desktop package metadata; release display labels may use
the four-part Windows version followed by a prerelease suffix.

> The repository previously hosted an earlier Slothy agent harness (releases
> 0.1.0 – 0.3.0). This line continues with the current desktop application and
> keeps that history for reference.

## [0.1.4.1-exp.1] — 2026-11-09

Development exploration prerelease. Not production-stable; use with backups.

### Changed

- DSH-style ordered static prompt sections and checkpointed operational snapshots:
  unchanged context reuses its position; changes append; compaction restores it.
  Source revocation, scope isolation, bounded retention and approval revalidation remain enforced.
  Long-term-memory services and personalization storage remain unchanged.
- DeepSeek no longer replays reasoning from final answers into later prompts;
  tool-call reasoning required by the protocol remains available.
- Corrected context accounting for system, real user turns and named references.
- Isolated provider-catalog model-picker styles so the composer model/effort control
  aligns with Send in Chat, Work, multiline, attachment and narrow-window layouts.
- Default prompt version: `2026-11-09.context-v1`.

### Added

- Decision-model configuration in Settings → API: choose any configured model to drive
  routing/tools/context; clearing the selection returns to chat-model self-driving, and
  decision-model failures now explicitly hand control back to the chat model.
- Provider model discovery is now opt-in: fetched catalogs are shown with search,
  multi-select, select-all/clear and Added/Verified states; discovery never overwrites
  user-configured models.
- Added-model validation calls each selected model with a tiny real request (64-token cap,
  three-way concurrency), supports partial success and keeps failed models in the catalog
  with their error and duration.
- Runtime observability: rotating application/error logs and per-session JSONL model-call
  snapshots under `data/logs/`, with prompt/context token categories, provider usage,
  cache hit ratio, tools, parameters, status, duration and credential redaction.
- Work Details tab in the right session panel with session/turn/model-call hierarchy,
  horizontal context bars, usage/capacity information and persisted history reload.
- About Slothy → View logs opens the application-managed log directory via a fixed IPC path.
- DeepSeek Search: a `deepseek_search` tool calls the official `POST /v1/tools/search`
  API with `query`, `count`, `ori_question` and `need_content`, normalizes sources and
  publishes a Settings → API key editor with save/test/delete, DPAPI storage, model-key
  fallback and dynamic tool registration. Missing keys disable the tool without affecting chat.
- Reviewed high-risk tools now explicitly instruct the model to call the tool instead of
  asking for natural-language confirmation; the runtime approval dialog pauses the run,
  and denials return `USER_DENIED` results recorded as rejected without retries.
- Reasoning visualization: vendor `reasoning_content` streams as a separate collapsible
  “思考过程 / Reasoning” block above the answer, auto-collapses when reasoning completes,
  persists with archived turns, and never enters prompt/history message content.
- MiMo native web search: supported MiMo v2.6 models receive the provider `web_search` tool; `LLM_WEB_SEARCH`
  (`auto`/`native`/`off`) applies only to supported MiMo models and falls back silently; DeepSeek uses the independent Search API tool above.
- `workspace_delete`: reviewed Chat/Work file/directory deletion with path
  normalization, symlink/root/private-path rejection, recursive confirmation and a
  high-risk approval hint. Deletion revalidates the target at execution time.
- Drag-and-drop attachments in chat, with an overlay, multi-file validation, pending
  list handling and PDF text extraction (pypdf) alongside TXT/Markdown/DOCX/JSON/CSV.
- About page: “Clear all local data” with an explicit `确认删除` confirmation gate,
  backend shutdown, managed-data purge, browser-storage cleanup, residual reporting
  and a restart action.
- Model-driven decision layer (S2): one `decide` node between `prepare_run` and
  `load_profile`, a dedicated OpenAI-compatible decision model configured by
  `DASHSCOPE__*` / `DECISION_*`, deterministic guards that clamp route/tools/context
  and rules fallback. The decision model is selected in Settings → API; with no selection the chat model decides autonomously, and a failed decision call hands control back to the chat model. The plan is persisted in graph state and metrics.

### Fixed
- MiMo accounts without the Web Search plugin now transparently retry without the
  native search tool, so ordinary chat no longer fails with `webSearchEnabled is false`.
- A failed model run that leaves an unfinished checkpoint no longer blocks the
  conversation with `Resume or clear the previous run first`; the next message
  rebuilds a fresh run from archived turns.
- Memory learning no longer wedges its durable queue when profile projection fails:
  projection is bounded by a timeout, invalid stored values cannot break the whole
  projection, and failed projection is logged while the analyzed turns still settle
  so the next batch can retry.
- The memory page no longer re-renders its whole frame on every learning poll:
  learning status and the saved-memory list moved into child components with their
  own state; the page header is rendered once and only dynamic regions update.

## [0.1.4.1-beta] — 2026-11-07

### Added
- Memory V3: model-extracted typed concepts, evidence-backed directed facts,
  independent per-session reinforcement and local FTS5/alias/one-hop recall.
- Deterministic identity answers and bounded memory diagnostics in chat, history,
  approval and the context panel; concept and relation frequencies shown separately.

### Changed
- Manual profile editing and autonomous learning now write the same fact service;
  attributes project identity, target-specific skills, preferences, habits and goals.
- Explicit corrections create new versions; capacity archives unprotected facts.
  Deleted evidence and historical profile bodies are erased and cannot replay.
- Strength is independently f/(f+c), default c=4. No TTL/time decay or embedding
  dependency. Prompt version is `2026-11-07.memory-v3`.
- First V3 startup resets all three business databases once without migrating or
  relearning old messages. Provider keys, connector configuration files, appearance
  preferences and real workspace files remain; old browser business caches clear.
- Display version `0.1.4.1-beta`, npm `0.1.4-beta.1`, Windows `0.1.4.1`.
- Verification: 424 backend tests (41 V3 cases), 43 frontend plus 3 desktop
  directory tests, Ruff, TypeScript, dark/light component and Electron checks,
  actual packaged backend acceptance, production bundle audit and SHA-256 verification.

### Release
- Windows x64 installer: `slothy-0.1.4.1-beta-x64-Setup.exe` (164,306,334 bytes).
  SHA-256: `b2d32a98f5066b78ce5860f56cc11763edd4b1355c7891bf7f8e4ac6e0d3f141`
  (also in `SHA256SUMS-0.1.4.1-beta.txt`; blockmap included).
- Display version `0.1.4.1-beta`, package SemVer `0.1.4-beta.1`, Windows numeric
  file version `0.1.4.1`; packaged manifest and lockfile agree.
- Production audit: 24 app entries, 956 backend files, 7,145 embedded Python
  modules, 0 prohibited entries.
- Verification: 424 collected backend tests (423 passed, 1 skipped), 43 frontend
  plus 3 desktop directory tests, Ruff, TypeScript, packaged-backend acceptance,
  both desktop startup smoke scenarios, dark/light checks and SHA-256 verification.

## [0.1.3.3-beta] — 2026-11-06

### Added — 2026-11-07 rebuild
- **Composer commands**: 20 fixed `/` actions and the plus button share a grouped,
  searchable menu. File upload moves into that menu and reuses the current picker;
  model, workspace, settings, navigation and streaming use their existing interfaces.
- **Explicit references**: `@` selects folders, files, owned conversations and
  plugins. Server revalidation, bounded low-trust L3 context, reference chips,
  archive/fork/review provenance and local preflight counts are included.
- **Context and artifacts**: confirmed `/compact` preserves the archive and two
  recent turns; `/export` reuses template export, journaling and authenticated downloads.
- **Run control**: stop handles survive page navigation. Resumption uses the
  server's original message and run identity, including after an edited retry fails.
- Verification: 383 backend and 42 frontend tests, Ruff, TypeScript, dark/light
  interactive desktop checks, IME/paste/keyboard, confirmation and stop/resume checks.

### Changed
- **Tool layer P0–P7**: built-ins, attachments and connectors share descriptor
  metadata, registry selection and a gateway for typed results/errors,
  risk-based approval, sequential execution, budgets and persisted measurements.
- Work exposes 9 default tools and Chat 7. Workspace reads/search/writes,
  md/txt/csv/json/docx template export, timezone lookup and reviewed `run_python`
  replace the old command/document interfaces. Hidden aliases resume old
  checkpoints; executable connectors are Work-only and Skill reads are read-only.
- Journaled file effects verify existing artifacts before reuse, block uncertain
  recovery, retain bounded managed runs and support authenticated downloads.
  Optional HTTPS retrieval is off by default and enforces a domain allowlist,
  public DNS addresses, pinned connections and redirect checks.
- Approval, activity and connector views expose risks, targets, artifacts and
  tool measurements while retaining historical text. Prompt/schema changes use
  one default prompt-version bump to `2026-11-06.tools-v2`.
- Verification: 368 backend and 36 frontend tests, Ruff, TypeScript, production
  frontend and frozen-backend builds, plus frozen-runtime acceptance pass. A
  25-case live proposal comparison selects the correct tool in 100% of new-tool
  cases versus 60% for the old set; shared-capability selection is 100% for both.
  This measures tool proposals, not end-to-end task completion.

### Release — 2026-11-07 rebuild
- Windows x64 installer: `slothy-0.1.3.3-beta-x64-Setup.exe` (164,157,898 bytes).
  SHA-256: `DE2276772DD3797ECA3F069615BEAD99A8CDCE886209118D41D07B399314C2EB`.
- Display version `0.1.3.3-beta`, package SemVer `0.1.3-beta.3`, Windows numeric
  file version `0.1.3.3`; the lockfile and packaged manifest agree.
- Production audit passes with 24 app entries, 956 backend files and 7,139
  embedded Python modules, including zero prohibited modules. Acceptance of
  the actual bundled backend, including commands/references, and both desktop
  startup smoke scenarios passes. The original pre-composer installer is archived.

## [0.1.3.2-beta] — 2026-11-06

### Added
- **First-run model setup**: an accessible, theme-aware glass onboarding overlay with
  provider templates (DeepSeek, MiMo, OpenAI, Anthropic, Kimi, Qwen) and custom
  OpenAI/Anthropic connections. It appears only while `setup_required` is true, shares
  validation/model-discovery/save logic with the settings editor, offers retry on
  configuration read failures, and opens the workspace without reloading the app. The
  welcome screen can switch the UI language.
- **Connector imports**: separate Local Tools, MCP and Skills management, per-item
  durable enable switches, JSON/ZIP imports, real stdio/Streamable HTTP/SSE MCP
  discovery and reviewed calls, plus bounded Skill reference-file access.
- **Autonomous LLM memory learning** (`core/app/memory/learning.py`): independent
  background graph, exact source evidence, scoped automatic persistence, duplicate
  merging, preference replacement, versioned profile projection and relationship updates.
- **Durable learning controls**: event wake-up, startup recovery, bounded retry/dead
  letters, idempotent commit/ack recovery, daily usage reservations, adaptive batch
  bounds and deletion/rejection exclusions. Late results cannot override a disabled
  setting or changed source state.
- **Live memory settings**: auto/review/shadow/off, model selection, confidence,
  source corroboration, active count and token budget now apply. Background model
  usage contributes to activity charts without adding chat turns; unknown usage stays partial.
- **Message preview sizing and theming**: SVG/HTML fill conversation width and use
  content-driven height, live application theme/font propagation and isolated interaction.
- **Reliable per-message copy**: icon-only controls for user and assistant messages
  in both Chat and Work, native desktop clipboard IPC and browser fallback with
  independent success/error feedback.
- **Appearance controls**: default Slothy green (`#60DD07`), a persistent custom accent
  picker with HEX validation and readable light/dark accent text. Existing saved palette
  choices are preserved.
- **Conversation branching plan**: message-boundary branching, independent approval,
  attachment references and memory/usage accounting are documented for a future iteration.
  No new branching implementation is included in this change.

### Fixed
- **Welcome focus ring**: extra vertical spacing prevents the scroll container from
  clipping the rounded outline around the selected setup path.
- **Dark dropdowns**: graph relationship choices and other native select options
  explicitly inherit the application theme instead of mixing light backgrounds with
  light text.

### Release
- Installer `slothy-0.1.3.2-beta-x64-Setup.exe` (163,747,501 bytes), SHA-256
  `87A84C022D1A6D2EBDE0D16BD4FB2386E1DC49C359655D66E7B38CE5CE23C524`.
- Application and installer display `0.1.3.2-beta`; package metadata uses
  `0.1.3-beta.2` and the Windows numeric file version is `0.1.3.2`.
- Verification: `npm run build`, 32/32 frontend tests, desktop smoke, bundled-backend
  acceptance and production audit (24 app entries, 351 backend files, 7,105 embedded
  modules, 0 prohibited) pass; backend regression 271 tests.

## [0.1.3] — 2026-11-05

### Added
- **Layered prompt assembly** (`core/app/agent/prompting/`): L1 stable system
  instructions, sorted tool instructions and a session-frozen profile snapshot;
  L2 append-only history with batch summaries; L3 per-turn memory/profile/attachment
  references at the tail. Explicit cache breakpoints on Anthropic, none on
  OpenAI-compatible providers.
- **Prompt cache telemetry**: layer hashes, byte sizes, plan ids, profile/history
  versions and reported cache-read/write tokens in run metrics; cache statistics
  in `GET /runtime`.
- **L2 history compression**: summaries with version and covered-turn counts,
  batched compression (8 turns after a 24-turn threshold) instead of per-turn
  trimming; async summary model configurable via `HISTORY_SUMMARY_MODEL`.
- **Memory configuration contract** (`GET/PATCH /settings/memory`): 13 preconfigured
  options with revision-checked atomic updates. The independent planner was not
  connected in this release; the API and UI stated that boundary explicitly.
- **Workspace directories**: workspaces map to real folders with overlap checks;
  native directory picker in Electron; per-directory tool instances and artifact
  roots (`.slothy/artifacts/runs`), without changing the process working directory.
- **Memory graph polish**: typed-neighbourhood initial layout with weak
  same-type clustering, stronger spring for real edges, drag and reduced-motion
  support.

### Changed
- Profile snapshots are frozen per session; learning and preference edits flow
  through the L3 delta so L1 stays cache-stable.
- Graph API returns totals/truncation and supports focused queries; links can be
  directed or undirected and can be deleted.
- Documentation restructured and published bilingually under `docs/`.

### Fixed
- Memory graph edge producer (shared-conversation relations, startup reconcile,
  manual link creation/deletion) and archived-memory visibility.
- Profile editor with optimistic concurrency; manual memory projection; profile
  version cap and cursor pagination; scoped attribute injection; approval resume
  re-checks the personalization switch.
- Traceability survives reloads (`used_memory_ids`, `profile_version` stored with
  archived turns and copied on fork).

## [0.1.2] — 2026-11-04

### Added
- User profile editor, profile version history (last 200, cursor paginated) with
  optimistic concurrency and `409 VERSION_CONFLICT`.
- Confirmed profile attributes are injected into the model context with source
  memory ids and scope filtering.
- Manual memories participate in the same confirmation/projection flow;
  conflicting preferences are superseded instead of coexisting.
- Memory relationship graph with automatic `shared_conversation` links and
  user-created links.
- Learning status, retry/error tracking and “reanalyze recent turns”.

### Fixed
- Deleted memories are redacted from historical profile snapshots while keeping
  deletion counters.
- Personalization switch is re-checked on every model call, including approval
  resume.

## [0.1.0] — 2026-11-03

### Added
- Initial MVP: Electron + Vue desktop shell, local FastAPI backend, LangGraph
  online agent with SQLite checkpoints, human-in-the-loop tool review, long-term
  memory with human confirmation, versioned user profile, workspace management
  and built-in tools (calculator, financial calculator, constrained commands,
  document generation, uploaded-file reading).
