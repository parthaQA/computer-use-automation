# Computer-use automation

Record-once / replay-many layer for legacy back-office UIs that have **no API**.
The model discovers a path; a typed capability is what production invokes.

This is the interface.ai take-home shape: a working mock core, a modular
framework with a real artifact schema, and seams for web → desktop and
single-tenant → vendor-product overlays. Discover/compile LLM wiring is the
next vertical slice — the ports are already in place.

## Layout

```
apps/mock_core/     Northstar MEMSYS — hostile proxy target (iframe, tables, no test IDs)
src/cua/            Framework package
  contracts/        Capability + RunResult (the evaluation centerpiece)
  surface/          SurfaceDriver port + Playwright adapter
  store/            File store today; registry tomorrow
  policy/           Allowlist, irreversible gate, redaction
  replay/           Deterministic interpreter (no LLM)
  discover/         LLM observe-decide-act (interface; not wired yet)
  compile/          Trace → capability
  session/handoff/  Live session control_owner AUTOMATION | HUMAN
  tenant/           Per-institution overlay, not re-record
  evidence/         JSONL sink
  orchestrator.py   Mode owner
  cli.py            cua validate | list | show
capabilities/       Versioned artifacts (bound to vendor_app, not tenant)
policies/           Allowlist
evidence/           Run traces (binaries gitignored)
docs/architecture.md
```

## Setup

Python 3.10+. From the repo root:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev,mock]"
copy .env.example .env
```

### Target app (Northstar MEMSYS)

```bash
cd apps/mock_core
pip install -r requirements.txt   # or use the root extra: pip install -e ".[mock]"
uvicorn main:app --reload --port 8000
```

Open http://127.0.0.1:8000 — lab operator **JLEE** / **demo**.

Happy path: inquiry `12345` → savings `1,204.55` → Open Sub-Account →
confirmation SUB04 → Post New Share.

Exceptions: `99999` not found, `77777` permission denied, `88888` locked,
`?inject=timeout|dialog|slow`. Full table in `apps/mock_core/README.md`.

### Framework CLI

```bash
cua version
cua validate capabilities/lookup_savings_balance.v1.json
cua list
cua show lookup_savings_balance
pytest
```

## Scaling without building the plumbing

- **SurfaceDriver** is the seam: web now, desktop later, same `ActionType`.
- **CapabilityStore** is a port: files now, multi-tenant registry later.
- Artifacts bind to `vendor_app` + version; **tenant overlays** specialize locators.
- Replay never calls the LLM. HITL pauses the **same** session.
- Irreversible steps (`Post New Share`) require human approval in policy.

Do not add queues or clusters until the discover → artifact → replay slice is real.
