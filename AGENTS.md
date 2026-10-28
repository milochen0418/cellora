# AGENTS.md

This file describes how an AI coding agent (and humans) should work in this repository.

## 0) Always read the docs first

Before making changes or running commands, **read all README.md files in this repo** and follow them.

- Repo overview & dev workflow: `README.md`
- E2E test runner conventions: `testcases/README.md`

If you already read them earlier in the session, **re-check** when unsure.

## 1) Python / Poetry requirements (strict)

This project targets **Python 3.11** (the range allows up to 3.14: notebooks import numpy, whose
line for 3.11 has no 3.15 wheels).

- Prefer Poetry-managed environments.
- If Poetry selected another Python, switch it:

```bash
poetry env use python3.11
poetry env info
poetry install
```

If `python3.11` is not available on PATH, use an absolute path to the 3.11 executable.

## 2) Default rule: use `poetry run` for scripts

When running any repo scripts, prefer:

- `poetry run <command>`

This ensures the correct virtualenv + dependencies are used.

## 3) Running the app: prefer `reflex_rerun.sh`

When you need to start/restart the Reflex app during development, **prefer the repo helper script**.

Typical usage:

```bash
poetry run ./reflex_rerun.sh
```

Notes:
- Prefer `poetry run ./reflex_rerun.sh` over `poetry run reflex run`.
- It runs on the expected ports: frontend `3000`, backend `8000` (it kills whatever holds them).
- Other apps on this machine may own 3000/8000. Then run on other ports and point the frontend at the backend: `REFLEX_API_URL=http://localhost:8150 poetry run reflex run --frontend-port 3150 --backend-port 8150`.
- Set `CELLORA_HOME` to a scratch folder when trying things, so test notebooks don't land in your real ones.

## 4) Running E2E suites: prefer `run_test_suite.sh`

E2E suites live under `testcases/<suite_name>/run_test.py`.

Do **not** run Playwright suites by starting/stopping the server manually unless you have to.
Use the repo runner, which starts/stops the Reflex server, sets `OUTPUT_DIR` and `CELLORA_HOME`, and collects artifacts:

```bash
poetry run ./run_test_suite.sh smoke_home
```

Optional environment variables (see `testcases/README.md`): `BASE_URL`, `BACKEND_URL`,
`FRONTEND_PORT` / `BACKEND_PORT`, `HEADLESS=0`.

Unit tests: `poetry run pytest`.

## 4.5) How Cellora is put together

- **The cells are a shared Yjs document**, one per open notebook, held by the server (`cellora/docs.py`, pycrdt) and synced with every browser over `/_cellora/yjs/<id>` (y-websocket protocol). Its layout (cells, outputs, run status) is described at the top of `docs.py`; `assets/cellora/ydoc.js` and `notebook.jsx` read and write the same layout, so change both together.
- **pycrdt counts text positions in UTF-8 bytes**, Yjs in UTF-16 units, Python in characters: `docs._replace_text` converts. Never index a `pycrdt.Text` with a Python string index.
- **A document lives as long as the process holds it.** Every restart (and the idle close) builds it from the .ipynb again, so a tab that stayed open holds a copy with other item ids: `LiveNotebook.serve` refuses that copy (`_from_another_life`, close code 4409) and `ydoc.js` `reset()` takes the document again, carrying what the tab typed meanwhile. Never merge two documents built from the same file: every cell comes twice.
- **The kernel writes outputs into the document** (`cellora/kernels.py`): never write outputs through Reflex state. Everything shown around the cells (title, people, kernel, variables, call) is the notebook's room, `NotebookRoom` (`rx.SharedState`), changed from outside the tabs' events through `rooms.with_room` / `rooms.push_later`.
- **The browser side is plain assets** (`assets/cellora/*.jsx|js`, imported by `importable_path`, npm packages via `lib_dependencies` in `cellora/components/notebook_view.py`). Monaco loads from jsdelivr (pinned in `editor.js`).
- **The kernel's own helpers** (the table format, `%%sql`, `__cellora_vars()`) are in `cellora/kernel_boot.py`, sent as text when a kernel starts.
- **AI**: `cellora/ai.py` (Claude with `ANTHROPIC_API_KEY`, else Ollama), `cellora/context.py` (what the model is told). Ollama fills in the middle only with a suffix: `_ollama_fim` always sends one.
- Words of the UI: `cellora/i18n.py` (English, 繁體中文); the notebook component gets the same dict as `labels`.
- `backend-paths.txt` sends `/_cellora/` to the backend behind re-ddns nginx (container restart after changing it).

## 4.6) The re-ddns dev-mode install

- `smart-app-cellora` bind-mounts this checkout and hot-reloads on **any** file change in it (`.md`, `.png` under `docs/` or `testcases/` too). A reload restarts the backend: kernels stop and open notebooks are saved and reloaded. Keep test output outside the checkout (`OUTPUT_DIR=...`) when testing against `https://cellora.reflex-ddns.com`.
- `pyproject.toml`, `rxconfig.py`, `poetry.lock` are copied into the container at start: changes to them need `docker restart smart-app-cellora`.
- Diagnose with `docker logs smart-app-cellora`.

## 5) Expectations when acting as an agent

- Prefer the smallest, focused change set.
- After code changes, run the closest relevant check (at minimum the related suite via `run_test_suite.sh`).
- Report outcomes: what you ran, pass/fail, and where artifacts/logs were written.

## 6) Smart way to help the Agent to debug

```
poetry run ./reflex_rerun.sh --loglevel debug
```

In the browser console, `monaco.editor.getEditors()` lists the cells' editors.
