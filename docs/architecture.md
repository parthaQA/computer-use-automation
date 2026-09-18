# Architecture

The calling agent decides *what* to do. This system is *how* it does it
inside a legacy UI with no API.

```
Goal / capability_id + params
            │
            ▼
      Orchestrator ── policy gate
            │
     DISCOVER | REPLAY | ESCALATE
            │
            ▼
      SurfaceDriver.observe() / act()
        web: Playwright + AX tree
        desktop (later): UIA / AX API, same ActionType vocabulary
            │
            ▼
   FileCapabilityStore today
   Registry + tenant overlay tomorrow
```

## Why these packages

| Package | Responsibility | Production swap |
|---|---|---|
| `contracts` | Artifact + result schema | unchanged |
| `surface` | Perceive / act on a UI | add `desktop` driver |
| `store` | Persist capabilities | SQL/registry |
| `policy` | Allowlist, risk, redaction | OPA / dual-control |
| `replay` | No-LLM interpreter | same, more recoverers |
| `discover` / `compile` | LLM loop → artifact | same port, better model |
| `handoff` / `session` | Live control transfer | operator queue + screencast |
| `tenant` | Overlay, not re-record | per-institution aliases |
| `evidence` | JSONL traces | object store + OTel |
| `apps/mock_core` | Proxy target | real core, still no API |

Do not put queues, k8s, or multi-tenant plumbing in v0. Keep the ports.
