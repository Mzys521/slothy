# Changelog

All notable changes to Slothy are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
semantic versioning for the desktop application.

> The repository previously hosted an earlier Slothy agent harness (releases
> 0.1.0 – 0.3.0). This line continues with the current desktop application and
> keeps that history for reference.

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
- **Memory configuration contract** (`GET/PATCH /settings/memory`): 13 planner
  options with revision-checked atomic updates. The planner itself is not enabled
  yet, and the UI states that explicitly.
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
