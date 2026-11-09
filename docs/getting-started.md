# Getting started

This guide takes you from a fresh clone to a running desktop app, and then to a
packaged installer.

## Requirements

| Component | Version | Notes |
|---|---|---|
| Windows | 10 / 11 x64 | Primary supported platform (packaging is Windows-first) |
| Python | 3.11+ | Backend and tooling |
| Node.js | 20+ with npm | Desktop shell and renderer |
| Git | any recent version | Clone and updates |

Disk: ~2 GB for dependencies and build output. The app data directory is small.

## 1. Clone the repository

```powershell
git clone https://github.com/Mzys521/slothy.git
cd slothy
```

## 2. Set up the Python backend

```powershell
cd core
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Optional, only if you want Excel / PDF / PPTX document generation:

```powershell
python -m pip install -e ".[documents]"
```

Create your local environment file from the template:

```powershell
Copy-Item .env.example ..\.env
```

`core/.env` is also supported and overrides `../.env`. Environment variables beat
both files. `.env` is git-ignored and must never be committed.

### Choosing a model provider

You can configure a provider two ways — pick one:

1. **In the app (recommended):** on first launch a centered onboarding overlay
   appears while no provider is configured. Pick a template (DeepSeek, MiMo,
   OpenAI, Anthropic, Kimi, Qwen) or a custom OpenAI/Anthropic-compatible
   connection, paste the key and save — the app opens the workspace. You can
   change providers later under **Settings → API**. Keys are stored with Windows
   DPAPI in `providers.json` inside the data directory. An optional independent
   **DeepSeek Search API key** can be saved/tested/deleted there; otherwise the DeepSeek
   model key is reused, and without either key `deepseek_search` is not exposed. High-risk
   tool calls pause for the runtime approval dialog, not a normal chat confirmation.
2. **In `.env`** for backend-only runs and the CLI examples:

```ini
# .env — choose one provider group; keys of other groups are ignored
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat
# DEEPSEEK_API_URL=https://api.deepseek.com

# MiMo
# LLM_PROVIDER=mimo
# MIMO_API_KEY=...
# MIMO_MODEL=...

# Any OpenAI-compatible endpoint / proxy
# LLM_PROVIDER=openai_compatible
# OPENAI_API_KEY=...
# OPENAI_API_BASE=https://api.openai.com/v1
# OPENAI_MODEL=...
```

`LLM_MODEL`, `LLM_API_KEY` and `LLM_BASE_URL` are optional generic overrides. The
model name must be set explicitly; Slothy never silently switches models.

Optional decision model: choose a configured model under **Settings → API → Decision
model**. It uses that provider's key and base URL. With no selection the chat model
decides autonomously; if the decision model is unavailable, invalid or over budget,
control returns to the chat model. Plans never bypass tool approval or enter
prompt-cache text. Backend-only runs may still configure `DASHSCOPE__*` / `DECISION_*`.

## 3. Install the desktop dependencies

```powershell
cd ..\web
npm install
```

## 4. Run in development

```powershell
npm run dev
```

What happens:

1. Vite starts the renderer dev server on `127.0.0.1:5173`.
2. Electron starts with that dev URL (main process: `web/electron/main.cjs`).
3. The main process spawns the backend from `core/.venv/Scripts/python.exe
   core/desktop/entry.py`, with a random loopback port and a per-launch token.
4. The renderer talks to `http://127.0.0.1:<port>/api/v1`; the main process
   injects the token for `/api/v1/*` requests.

First run checklist:

- **First-run setup**: the onboarding overlay appears only while no provider is
  configured. Choose a template or a custom connection, add the API key, save, and
  the app opens the workspace without a page reload.
- **New conversation**: send a message and confirm the answer streams.
- **Tool approval**: ask for a document or a shell command; the run pauses and
  shows the exact call — approve to continue.
- **Memory V3**: state a preference, habit, goal or fact. The background model
  extracts typed concepts and evidence-backed directed facts; identity questions
  answer from reliable facts, and the context panel shows the provided evidence.
  On first V3 startup the three business databases reset once (old messages are not
  relearned) — export anything you need before upgrading.

### Composer commands and references

Type `/` in the composer to open the 20 grouped, searchable commands (session, model,
context, artifacts and help); file upload now lives in the same plus menu. Type `@`
to reference a workspace folder or file, an owned conversation, or an enabled plugin:
the server revalidates ownership and scope, then injects bounded low-trust L3 data.
`/compact` and `/export` require confirmation, and plugin tool calls keep the normal
approval policy. Files can also be dropped onto the conversation: TXT, Markdown, PDF,
DOCX, JSON, CSV and similar documents are validated (8 files, 2 MiB each) and kept as
pending attachments; PDF text is extracted locally before sending.

## 5. Data locations

| Mode | Data directory |
|---|---|
| Development | `core/data/` (checkpoints, session archive, workspace memory, artifacts) |
| Installed app | `%APPDATA%\slothy\data\` |

Both contain `checkpoints.sqlite`, `checkpoints.sessions.sqlite`,
`checkpoints.workspace.sqlite`, `providers.json` and `artifacts/`. **Memory V3**:
the first V3 startup resets the three business databases once; provider keys,
connector configuration files, UI preferences and workspace files remain. Delete the
directory to reset the app; back it up to keep your memory.

## 6. Build a Windows installer

```powershell
cd ..\web
python -m pip install "pyinstaller>=6"    # required by build:backend, not in [dev]
npm run build
```

The build runs four steps: renderer build (`vite build` + `vue-tsc`), backend
freeze (PyInstaller, `core/desktop/service.spec`), release preparation, and
NSIS packaging (`electron-builder`). The installer is written to
`web/release/slothy/`.

Useful variants:

```powershell
npm run build:web        # renderer only
npm run build:backend    # frozen backend runtime only
npm run build:dir        # unpacked app directory, no installer
```

## 7. Tests and checks

```powershell
# backend
cd core
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check app tests

# desktop
cd ..\web
npm run typecheck
npm run production:audit
```

Note: a few tests spawn real subprocesses and create named pipes. They need a
normal, unsandboxed Windows session; under a restricted sandbox they fail with
`WinError 5`.

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Window opens but every request fails | Backend did not start or token mismatch | Check the terminal for backend startup output; ensure `core/.venv` exists and dependencies are installed. |
| `Cannot find module 'electron'` | `npm install` was not run in `web/` | `cd web; npm install`. |
| `MODEL_UNAVAILABLE` / 503 | No provider configured, or model not in the catalog | Configure a provider under Settings → API, or check the `.env` group you selected. |
| Chat streams but tool runs never start | Tool call was rejected or is waiting for approval | Open the approval card / `GET /threads/{id}/approval`. |
| `VERSION_CONFLICT` when editing the profile | The profile changed since you loaded it | Reload the profile page and retry. |
| Build fails at `build:backend` | PyInstaller missing from the venv | `python -m pip install "pyinstaller>=6"`. |
| Tests fail with `WinError 5` | Sandboxed terminal blocks named pipes | Run the test suite in a normal terminal. |

## Next steps

- [Architecture](architecture.md) — how the pieces fit together.
- [API reference](api.md) — endpoints and SSE events.
- [Brand guidelines](brand.md) — colors, logo usage and voice.
