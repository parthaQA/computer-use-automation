# REPORT

## 1. Architecture

Two runtimes, one contract. **Discover** (LLM in the loop, rare) produces an
`ActionTrace`. **Compile** turns that into a versioned `Capability`. **Replay**
is a pure interpreter: no model in the decision loop. **Escalate** freezes the
same live session (`control_owner = HUMAN`) and resumes it later.

The orchestrator owns mode, policy, and timeouts. It does not import Playwright
or the LLM SDK. Perception/action go through `SurfaceDriver`. Persistence goes
through `CapabilityStore`. That is the scaling story: swap adapters, keep the
schema.

Trade-off: a single-process CLI plus a local mock is enough for the take-home.
Splitting discover/replay/HITL into services is a deployment choice, not a
schema change.

## 2. Artifact schema

`capability.v1` is agent-invocable: `id`, `vendor_app`, `surface_kind`,
`risk_class`, typed `params` (with `pii_class`), typed `outputs`, ordered
`steps`, ranked `LocatorStrategy` lists, checkpoints, `on_error`, optional
`overlays[]`, and `lineage`.

Locators rank `role_name` → `label` → `text` → `name_attr` → `ax_path` →
`visual` last and flagged `unstable`. Session-scoped Playwright refs are
forbidden. The artifact is bound to a **vendor product**, not a tenant.

## 3. Determinism and error handling

Replay waits on actionability and asserts checkpoints (screen codes such as
`MEM02` / `SUB04`, or visible text). No `sleep`.

Result contract — do not conflate these:

- `succeeded` — checkpoint met, outputs returned
- `business_outcome` — `MEMBER_NOT_FOUND`, `PERMISSION_DENIED`, NSF, locked
- recoverable — `Continue Session`, `Dismiss` on a known interstitial
- `failed` / `escalated` — hard stop with step, expected, observed

MEMSYS encodes those outcomes as stable copy, not HTTP 500s.

## 4. Heterogeneity and multi-tenant

`SurfaceDriver` is the seam between “how we perceive a surface” and “the
recorded flow.” A desktop adapter implements the same `ActionType` against
UIA/AX. Capabilities stay semantic.

Hundreds of institutions run ~20 apps, many on one vendor product. Store a
base capability plus `TenantOverlay` (locator/text aliases, extra dismiss
dialogs, version range). Canonicalize `/member/12345` → `/member/:id`. Detect
drift via checkpoint fail rate, not by re-recording every tenant.

## 5. Escalation and handoff

Stuck = max steps, timeout, unknown dialog, irreversible step without
approval, or locator miss. `HandoffController.pause` sets `control_owner` to
HUMAN on the **existing** session and writes an `Intervention` (capability,
step, reason, screenshot note). Resume restores AUTOMATION. A headed browser
plus CLI “press Enter” is a real control transfer; a co-browse console is out
of scope.

## 6. Safety

`policies/default.json` allowlists hosts and actions. `irreversible_requires_human`
blocks unattended `Post New Share`. Secrets never belong in artifacts; member
IDs are `pii_class=identifier` and are not logged raw when redaction applies.
AX/page text is untrusted (prompt injection). Limits: regex redaction is not
a DLP product; production needs vault injection and WORM audit.

## 7. Cuts

Not built yet: LLM discover loop, trace compiler, Playwright e2e of replay,
operator UI, desktop driver, capability catalog HTTP API. Next: wire Claude
Sonnet tools to `SurfaceDriver` against MEMSYS, compile `lookup_savings_balance`
from a real run, replay `member_id=99999` as `MEMBER_NOT_FOUND`, and pause at
SUB04 for HITL.
