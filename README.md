# Computer-use automation (cuaflow)

Discover a back-office UI **once** with an LLM in a visible Chrome window. After that, **replay** the same task with Playwright only — no model, no clicks from the LLM.

The target is a local mock of MEMSYS (no production core, no public internet required).

```
You type a goal  →  first run records a workflow JSON  →  later runs replay that JSON
```

| Mode | What runs | Needs an API key? |
|---|---|---|
| **Discover** | Headed Chrome + LLM | Yes (`ANTHROPIC_API_KEY`) |
| **Replay** | Playwright clicks the saved JSON | No |

Do **not** put operator id or password in the goal text. They are read from `.env`. If the prompt contains those values, rewrite is blocked and the LLM is not called.

---

## How to set up and run

Python **3.11+**. From the repo root.

### 1. Create a virtualenv and install

Same packages on every OS. Only the venv activate / copy commands differ.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev,mock]"
python -m playwright install chromium
copy .env.example .env
```

If `Activate.ps1` is blocked, use Command Prompt instead: `.venv\Scripts\activate.bat`

**macOS (Terminal)**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mock]"
python -m playwright install chromium
cp .env.example .env
```

**Linux (bash)**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,mock]"
python -m playwright install chromium
cp .env.example .env
```

After activate, `python` is the venv interpreter on Windows, macOS, and Linux.

### 2. Keys and config (`.env`)

Edit `.env` (never commit it). Start from `.env.example`:

| Variable | Required for | What it is |
|---|---|---|
| `ANTHROPIC_API_KEY` | Discover only | Anthropic key so the LLM can drive Chrome |
| `ANTHROPIC_MODEL` | Discover only | Default `claude-sonnet-4-5` |
| `MEMSYS_OPERATOR` | Discover and replay | Lab sign-on id (already in `.env.example`) |
| `MEMSYS_PASSWORD` | Discover and replay | Lab sign-on password (already in `.env.example`) |
| `MEMSYS_BASE_URL` | Discover and replay | Mock origin, default `http://127.0.0.1:8000` |

Optional: `BROWSER_USE_API_KEY` if you want Browser Use instead of Anthropic (`CUA_LLM=browser-use`).

**Prompt rules**

- Say `open base_url` — never paste `http://127.0.0.1:8000`.
- Say `enter operator id and password` — never the real values.
- Member ids such as `12345` or `99999` **are** allowed in the prompt.

### 3. Run without live services (local mock)

You do **not** need a real credit-union core. Start the mock on the same host/port as `MEMSYS_BASE_URL`. Leave this terminal running. Open http://127.0.0.1:8000 to confirm the sign-on page.

**Windows (PowerShell)** — venv does not have to be activated if you call the interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.mock_core.main:app --host 127.0.0.1 --port 8000
```

**macOS**

```bash
python -m uvicorn apps.mock_core.main:app --host 127.0.0.1 --port 8000
```

**Linux**

```bash
python -m uvicorn apps.mock_core.main:app --host 127.0.0.1 --port 8000
```

On macOS/Linux, run that from an activated venv (or use `.venv/bin/python -m uvicorn ...`).

Replay and tests do **not** call Anthropic. Only discover needs the API key.

Unit tests (no Chrome, no mock, no key) — Windows, macOS, and Linux:

```bash
python -m pytest -q
```

---

## Demo path

Keep the mock running. Use two terminals: one for uvicorn, one for the commands below.

These `python -m cuaflow...` lines are the same on **Windows, macOS, and Linux** once the venv is active.

The goal: sign on, search member `12345`, read the savings page.

### A. Run the agent on that goal (discover)

Needs `.env` with `ANTHROPIC_API_KEY`. A Chrome window opens. Do not type in it unless you press **H** to take over.

```bash
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 12345 and click on search button"
```

This repo already has that flow recorded. The command above will **replay** it unless you force a new recording:

```bash
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 12345 and click on search button" --rediscover
```

**Expect:** `success` and a savings balance (HERNANDEZ, MARIA). Screen `MEM02`.

Printed lines to keep:

- `json:` → workflow file under `workflows/` (the artifact)
- `evidence:` → `evidence/lookup-savings-balance.discover/` (video/screenshot/logs when present)

### B. Replay the resulting artifact (no LLM)

Same goal, no `--rediscover` — catalog finds the JSON and Playwright clicks it:

```bash
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 12345 and click on search button"
```

Or replay the file directly (no rewrite LLM either):

```bash
python -m cuaflow.replay lookup_savings_balance --member-id 12345
```

**Expect:** same `success` / `MEM02`. Evidence lands in `evidence/lookup-savings-balance.replay/` including `replay.webm` when recording is on.

List recorded capabilities:

```bash
python -m cuaflow.replay --list
```

---

## What you should see

| Path | What it is |
|---|---|
| `workflows/*.json` | Saved click path (the replay artifact) |
| `workflows/catalog.json` | Maps a rewritten goal → that JSON |
| `evidence/<use-case>.discover/` | First-run pack (headed LLM) |
| `evidence/<use-case>.replay/` | Replay pack (Playwright, no LLM) |
| `runs/<run_id>/events.jsonl` | Step log for that run |

Console status is one of:

- `success` — page shows the expected result
- `business_outcome` — app said not found / denied / locked / insufficient funds (not a crash)
- `failed` — missing control, validation, or policy block

---

## Another goal (member not found)

Discover (or `--rediscover`), then the same prompt again to replay:

```bash
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 99999 and click on search button. verify the member record"
```

**Expect:** `business_outcome  MEMBER_NOT_FOUND` (exit 0). Evidence folder `evidence/verify-member-record.discover/` then `.replay/`.

Direct replay of that artifact:

```bash
python -m cuaflow.replay 3fea6845d296 --member-id 99999
```

More cases: `docs/use-cases.md`. Mock exception table: `apps/mock_core/README.md`.

---

## Notes

- Discover is headed only (policy blocks `--headless` on discover). Replay can add `--headless`.
- Press **H in the MEMSYS Chrome window** to take over; Enter on that page returns control. Do not put secrets in the terminal prompt.
- `--tenant lab` reads URL/app from `tenants/tenants.json`. Omit it to use `MEMSYS_BASE_URL`.

