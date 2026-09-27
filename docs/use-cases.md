# MEMSYS replay use cases

Run **one case at a time**. Wait for the printed result (and the `events:` path) before starting the next.

From the repo root, with `.venv312` active. Set `MEMSYS_BASE_URL` in `.env` (see `.env.example`). Web prompts say **open base_url** — never a host. Desktop prompts say **open app** — no URL. Pass `--surface web` or `--surface desktop`.

```powershell
# mock (if it is not already running) — host/port must match MEMSYS_BASE_URL
.\.venv312\Scripts\python.exe -m uvicorn apps.mock_core.main:app --host 127.0.0.1 --port 8000
```

If you are already in `apps/mock_core`:

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Sign-on uses **MEMSYS_OPERATOR** and **MEMSYS_PASSWORD** from `.env`. Do not put them in the prompt.

How to read the result:

- Console: `success …`, `business_outcome  CODE …`, or `failed  step=…  expected=…  observed=…`
- File: `runs/<run_id>/events.jsonl` — last `workflow_finished` line has `extracted` with `outcome`, `screen_id`, `detail`, `observed`, `outcome_code`, `step`, `expected`

Human control (only when a case says so):

- Press **H in the MEMSYS browser window**, not in the terminal.
- Finish the page, then press **Enter on that page** (or **Return to automation**).
- Approval popup (case 9): do **not** press H. Automation stops; you Approve or Cancel.

Cases 1–6 and 9 reuse recordings (replay, no LLM clicks). Cases 7–8 and 10–12 are new step lists, so the **first** run discovers.

---

## 1. Success — savings lookup

```powershell
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 12345 and click on search button" --surface web
```

Discover records through the **web Surface** (Chrome + LLM) and writes both CSS and desktop locators on the same JSON.

Desktop discover (same clicks, Tk window, no URL):

```powershell
python -m cuaflow.discover "open app, enter operator id and password, click on sign on button. search member id 12345 and click on search button" --surface desktop
```

Expect: `success` with `savings_balance` (HERNANDEZ, MARIA). Screen `MEM02`.

`extracted.outcome`: `success`

---

## 2. Business — member not found

```powershell
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 99999 and click on search button. verify the member record"
```

Expect: `business_outcome  MEMBER_NOT_FOUND` — not a crash. Exit 0.

`extracted.outcome_code`: `MEMBER_NOT_FOUND`

---

## 3. Business — permission denied

```powershell
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 77777 and click on search button"
```

Expect: `business_outcome  PERMISSION_DENIED` (PATEL, AISHA).

---

## 4. Business — membership locked

```powershell
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 88888 and click on search button"
```

Expect: `business_outcome  MEMBER_LOCKED` (WALKER, JAMES).

---

## 5. Hard failure — empty member ID

Replay fills `--member-id`; an empty value is the validation path.

```powershell
python -m cuaflow.replay workflows/lookup_savings_balance.json --member-id ""
```

Expect: `failed` with `expected=valid input accepted by the form` and `observed=Member ID is required`.

`extracted.step`: `click:Search`

---

## 6. Hard failure — non-numeric member ID

```powershell
python -m cuaflow.replay workflows/lookup_savings_balance.json --member-id ABCDE
```

Expect: `failed` with observed `Member ID must be numeric`.

---

## 7. Business — insufficient funds (discovers the first time)

Deposit `99999` is a different step list than `25.00`.

```powershell
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 12345 and click on search button. click Open Sub-Account. select product type 02 Secondary Shares. set opening deposit 99999. leave source share S00. click Continue."
```

Expect after Continue: `business_outcome  INSUFFICIENT_FUNDS`. Do not click Post New Share.

---

## 8. Hard failure — product type required (discovers the first time)

```powershell
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 12345 and click on search button. click Open Sub-Account. leave product type blank. set opening deposit 25.00. click Continue."
```

Expect: `failed` with observed `Product type is required`.

---

## 9. Human takeover — approval popup (already recorded)

```powershell
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. search member id 12345 and click on search button. click Open Sub-Account. select product type 02 Secondary Shares. set opening deposit 25.00. leave source share S00. click Continue.. on the confirmation screen click Post New Share. verify the confirmation number is shown"
```

When the overlay appears, automation **stops**. You finish: **Approve** then Post, or **Cancel**. Close the window when done. Do not press H unless you want control earlier.

Expect: `business_outcome  HUMAN_TAKEOVER`.

---

## 10. Recoverable — session timeout (discovers the first time)

```powershell
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. open base_url/main/inquiry?inject=timeout then search member id 12345 and click on search button"
```

First run: discover (LLM / HITL if it pauses). Second run: replay should click **Continue Session**, then look up 12345.

Expect on replay: `success` with the savings balance. Log line `Recoverable SESSION_TIMEOUT`.

---

## 11. Recoverable — unexpected system dialog (discovers the first time)

```powershell
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. open base_url/main/inquiry?inject=dialog then search member id 12345 and click on search button"
```

Replay should click **Dismiss**, then search.

Expect: `success`, with `Recoverable SYSTEM_DIALOG` in the log.

---

## 12. Recoverable — slow page (discovers the first time)

```powershell
python -m cuaflow.discover "open base_url, enter operator id and password, click on sign on button. open base_url/main/inquiry?inject=slow then search member id 12345 and click on search button"
```

The mock waits ~3s, then the inquiry page loads. Replay retries navigation rather than failing immediately.

Expect: `success` with the savings balance.

---

## Optional — H-key takeover (any headed replay)

During case 1, press **H in the MEMSYS window**. You should see one `HUMAN IN THE LOOP` block. Do what you need, then **Enter on the page**. Replay continues from the current screen. You do not need H for the approval popup in case 9.

---

## Web vs desktop (one artifact, two adapters)

`workflows/lookup_savings_balance.json` is the capability. Each step lists web CSS/xpath **and** desktop `name/` / `automationId/` locators. Replay `--surface` picks the adapter. Events still show which one ran.

### A. Web — Chromium against the mock

Mock must be running at `MEMSYS_BASE_URL` (default http://127.0.0.1:8000).

```powershell
python -m cuaflow.replay lookup_savings_balance --member-id 12345 --surface web
```

Expect Chrome (URL from `MEMSYS_BASE_URL`), `surface: web`, `surface_opened driver=playwright title=Chromium`.

```powershell
Get-Content runs\<run_id>\events.jsonl | Select-String "surface"
```

### B. Desktop — same JSON, Tk window titled MEMSYS Desktop

No mock server. A **desktop window** opens (green banner: DESKTOP APP — not a browser).

```powershell
python -m cuaflow.replay lookup_savings_balance --member-id 12345 --surface desktop
```

Expect: `surface: desktop`, `surface_opened driver=tk title=MEMSYS Desktop`, `surface_step` locators like `automationId/OPR` and `name/Search`. Screen `MEM02`, savings balance `1,204.55`.

