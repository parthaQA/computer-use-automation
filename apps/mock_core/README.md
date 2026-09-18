# Northstar MEMSYS 4.2 (mock core)

Local stand-in for a credit-union **core servicing console** with no API.
Built as the proxy target for the computer-use automation take-home.

The UI is intentionally hostile: iframe chrome, nested tables, HTML 4.01,
yellow 5250-style fields, **no test IDs, no ARIA, no semantic element ids**.
Labels sit in adjacent table cells. Buttons are `<input type="submit">`.

## Run

```bash
cd apps/mock_core
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open http://127.0.0.1:8000

Lab operator: **JLEE** / **demo** (shown on the sign-on screen).

Reset in-memory ledgers: `GET http://127.0.0.1:8000/lab/reset`

## Happy path (the goal to automate)

1. Sign on as JLEE.
2. Member Inquiry → member ID `12345` → Search.
3. Read **Current savings balance** (`1,204.55` on Regular Shares S00).
4. Open Sub-Account → product `02 Secondary Shares`, nickname optional,
   opening deposit e.g. `25.00`, source S00 → Continue.
5. Confirmation screen SUB04 → **Post New Share**.
6. Screen SUB05 shows confirmation number `NSC-YYYYMMDD-n`.

Second valid member: `12346` (Robert Chen, savings `8,910.00`).

## Deterministic exception states

These are app outcomes, not crashes. Replay should classify them.

| Input / inject | What you see | Class |
|---|---|---|
| Empty member ID | `Member ID is required` | validation |
| Non-numeric ID | `Member ID must be numeric` | validation |
| Member `99999` (or any unknown) | `Record not found for membership …` | business outcome |
| Member `77777` | Permission denied (record exists) | business outcome |
| Member `88888` | Membership locked | business outcome |
| Opening deposit `99999` on 12345 | `Insufficient available balance` | business outcome |
| Blank product type | `Product type is required` | validation |
| `?inject=timeout` on any `/main/*` URL | Session timeout interstitial | recoverable (`Continue Session`) |
| `?inject=dialog` | Unexpected system-message overlay | recoverable (`Dismiss`) |
| `?inject=slow` | ~3s delay then normal page | transient slowness |

Example: http://127.0.0.1:8000/main/inquiry?inject=timeout
(must already be signed on; load it in the **appf** iframe or as a top-level
page after sign-on).

## Why this surface

- Same shape as the brief: search → detail → multi-field form → confirmation.
- Stable layout, real runtime errors.
- No clean DOM: frames, tables, `name="MEMID"` not `#member-id`.
- Screen codes (`INQ01`, `MEM02`, `SUB03`, `SUB04`, `SUB05`) are usable
  checkpoints for deterministic replay.
- `Post New Share` is the irreversible step a production policy should gate.
