# E2E testcases

Playwright end-to-end suites, one per folder:

- `testcases/<suite_name>/run_test.py`
- `testcases/<suite_name>/output/` (screenshots and logs, ignored by git)

Each `run_test.py` exits `0` on success and non-zero on failure, and writes its screenshots into
`OUTPUT_DIR` (provided by the runner). Shared helpers (opening a browser, making a notebook,
reading and setting a cell's code, running a cell) are in `common.py`. Every suite makes its own
notebooks; the runner points `CELLORA_HOME` at a fresh folder in `OUTPUT_DIR`, so your notebooks
are not touched.

## Suites

| Suite | What it checks |
|-------|----------------|
| `smoke_home` | The getting-started tour: 9 cells, the outline; the first cell runs into an interactive table (12 rows), which sorts, filters and turns into a chart; `%%sql` on the DataFrame, a matplotlib picture, the deliberate KeyError; the `.ipynb` (with the table output) and `.py` downloads; the 繁體中文 switch. |
| `collab_live` | Two people on one notebook: both see "2 here"; typing reaches the other; both typing in the same cell at once end with the same text; the other's caret and cell marker show; a run's output and "who ran it" reach both; Shift+Enter's new cell and a new text cell appear for both; following someone; a closed tab disappears for the other at once. |
| `run_cells` | Run all stops at an error and cancels the cells after it; HTML, Markdown, LaTeX and JSON outputs; a progress bar rewriting its line; 300 000 printed lines cut at 1 MB; Stop; a crashing kernel (`os._exit`) shown as stopped, with a note in the cell, and Restart; the Variables panel. |
| `files_vars` | Upload a CSV in the Files panel, *Use* adds a cell that reads it and runs; the file downloads; an `.ipynb` imported from the home page keeps its outputs and title. |
| `reconnect_reload` | (Needs `CELLORA_NOTEBOOK_IDLE_MINUTES=0.05` for the server; skipped otherwise.) A tab's connection drops and it types on; the server closes the idle notebook and reads it from its file again when the tab is back: the tab takes that document (no cell doubled), what it typed meanwhile is there, a second tab sees the same, and both still see each other's typing. |
| `ai_assist` | (Needs Claude or Ollama; skipped otherwise.) The grey suggestion while typing and Tab; ⌘I writes a cell from an instruction, shown as a diff and accepted, then runs; Fix with AI repairs a NameError; the assistant panel's answer with code, inserted as a new cell. |
| `passcode` | (Needs `CELLORA_RUN_PASSCODE`; skipped otherwise.) Running asks for the passcode, refuses a wrong one, runs after the right one. |
| `call_everyone` | (Needs reflex_ddns_auth and an installed call app: run it against re-ddns.) A calls everyone, B is rung and accepts, both see "Join call · 2". |

## Running

Use the repo-level runner (it starts and stops the Reflex server and collects artifacts):

```bash
poetry run ./run_test_suite.sh smoke_home
poetry run ./run_test_suite.sh collab_live
poetry run ./run_test_suite.sh run_cells
poetry run ./run_test_suite.sh files_vars
CELLORA_NOTEBOOK_IDLE_MINUTES=0.05 poetry run ./run_test_suite.sh reconnect_reload
poetry run ./run_test_suite.sh ai_assist
CELLORA_RUN_PASSCODE=letmein poetry run ./run_test_suite.sh passcode
```

If ports 3000/8000 belong to another app, use others: `FRONTEND_PORT=3150 BACKEND_PORT=8150`.

To test a deployed instance (e.g. behind re-ddns), run a script directly:

```bash
BASE_URL=https://cellora.reflex-ddns.com OUTPUT_DIR=/tmp/cellora-e2e \
  poetry run python testcases/call_everyone/run_test.py
```

Keep `OUTPUT_DIR` outside the repo when the target is a re-ddns **dev-mode** install of this
checkout: that container watches the mounted source, so screenshots written under `testcases/`
hot-reload its backend in the middle of the test. On a deployed instance the suites make
notebooks there: delete them afterwards from the notebook list.

Optional env vars:
- `BASE_URL` (default `http://127.0.0.1:3000`) and `BACKEND_URL` (default: `BASE_URL`; the
  runner sets the local backend port)
- `HEADLESS=0` to watch the browsers
- `PW_SLOWMO_MS=100` to slow actions down
- `PW_TIMEOUT_MS=60000` to increase Playwright timeouts
- `PW_STACKS=1` to print the stack of page errors
