# Changelog

All notable changes to Slothy are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
semantic versioning for desktop package metadata; release display labels may use
the four-part Windows version followed by a prerelease suffix.

> The repository previously hosted an earlier Slothy agent harness (releases
> 0.1.0 – 0.3.0). This line continues with the current desktop application and
> keeps that history for reference.

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
