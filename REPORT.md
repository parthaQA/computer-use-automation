# Design report

This system does one job: **record a back-office click path once**, then **replay it many times** without asking a model to click again. The live target is a local mock of MEMSYS (no API, hostile HTML). Discover uses an LLM in a visible Chrome window. Replay is Playwright only.

---

## 1. Architecture

**Record once, replay many.** A user types a goal in action words (`open base_url, enter operator id and password, …`). That prompt never carries the real operator id or password; those come from `.env`.

Flow:

1. **Rewrite** — an LLM turns messy wording into a stable checklist. If the user typed credential *values*, this step is blocked and the model is never called.

2. **Catalog** — if that checklist already maps to a JSON file, we skip discover and **replay**.

3. **Discover** (first time, or `--rediscover`) — headed Chrome + LLM. Policy allowlists hosts and actions. A human can take the same browser with by entering "H", the control will go to Human operator and then press Enter to give the control back to automation.

4. **Artifact** — the run is saved as `capability.v1` JSON under `workflows/`, plus an evidence pack under `evidence/`.

5. **Replay** — Playwright (or the desktop surface) executes those steps. No LLM. Page text is classified into success, business outcome, validation, recoverable overlay, or hard fail.

**Key decisions**

- **LLM only in discover.** Production-shaped replay must be cheap, repeatable, and reviewable.

- **One JSON, two surfaces.** Each step can hold web CSS/xpath *and* desktop names. Replay picks the set for `--surface web` or `--surface desktop`.

- **Named locators, not pixels.** We do not click by (x, y) in production. Selectors are form `name`, xpath, visible text, then desktop `automationId`.

- **Outcomes are not crashes.** “Record not found” is `business_outcome`, not `failed`.

**Trade-offs**

We chose a small, reviewable slice over a full platform. That shows up in every layer:

| Choice | What we gain | What we give up |
|---|---|---|
| LLM **only in discover**; replay is Playwright | Cheap, repeatable, easy to grade. No model on the production click path | First recording needs an API key, a headed browser, and human time |

| **Headed** discover (headless blocked) | Operator can watch and press H in the same Chrome window | Cannot record in a typical CI agent; Jenkins can only **replay** |

| **Named locators**, not (x, y) | Survives small layout shifts; no production vision clicks | If every selector misses, we fail and rediscover — no auto-heal |

| **Canonical rewrite** then exact catalog match | Same clicks with different wording still replay after rewrite | Rewrite still costs a model call; similar-but-not-canonical goals still discover |

| **Files** (`workflows/`, `evidence/`, `runs/`) | Graders can open JSON and video with no database | No multi-user store, locking, or query history |

| **Allowlist** policy (hosts, actions, no secrets in the prompt) | Safe default on the mock; irreversible clicks need a human | The discover LLM can still do odd things *inside* the allowlist; not NeMo-class rails |

| **One JSON, web + desktop locators** | One recording can replay on Chrome or the Tk app | Desktop locators are inferred from web discover; a true native app may still need a second pass |

| **Tenant map** (URL/app only) | Reuse the same vendor artifact at another institution | Overlays do not handle a totally different UI; that is a new recording |

| **Mock MEMSYS**, not a live core | Runs offline; deterministic not-found / locked / timeout cases | Real cores will be slower, messier, and need extra allowlist hosts |

| **Business outcome ≠ fail** | “Member not found” is a valid result (exit 0) | Callers must read `outcome_code`, not only pass/fail |

| **Secrets in `.env`**, never in the user query | Evidence and prompts stay clean | Operators must set env vars; a typed id in the prompt is blocked |

Net: the project is easy to run and explain, and replay is trustworthy. It is **not** a low-cost, fully autonomous, multi-tenant SaaS — those are the Cuts / build-next items (local rewrite model, Redis semantic cache, Postgres, Jenkins, LangSmith).

---

## 2. Artifact schema

The saved file is `capability.v1` (`UserFlow` in code). It is meant to be read by a person, not only by Playwright.

| Field | Why it is there |
|---|---|
| `id` / `version` | Stable name so replay can find the file |
| `title` | The **user query** in action words (no secrets, no raw host) |
| `vendor` / `app` | Bound to the product (MEMSYS), not to one credit union |
| `surface` | Default adapter (`web` or `desktop`); replay can override |
| `inputs` | `member_id`, `product`, … Secrets (`password`, `operator`) are flags, not stored values |
| `outputs` | What the run may return (`screen_id`, `message`, balance, …) |
| `success.checks` | Verify **only** if the user asked. Search “found or not found” uses `capture=true` so both results are valid |
| `steps` | `navigate` / `click` / `change` / `keyDown` only |

A step lists **fallback locators** (CSS, xpath, desktop name). Values use placeholders (`{member_id}`, `{operator}`, `{base_url}/signon`). Passwords in the file are `<secret>x_password</secret>`, filled from `.env` at run time.

We shaped it this way so: (1) replay does not need the LLM, (2) the same file can run on web or desktop, (3) another institution can reuse it by changing URL/app, not by re-recording clicks.

Evidence packs (`evidence/<use-case>.discover|replay/`) sit beside the JSON: events, run log, screenshot, optional video, manifest. Text is scrubbed so operator id and password do not remain on disk.

---

## 3. Determinism & error handling

Replay is deterministic because it **does not decide**. It runs the recorded steps in order, fills `{member_id}` and env credentials, and waits for the same locators. Duplicate clicks are skipped. There is no vision click in the happy path.

After each step we read **page text + screen code** (INQ01, MEM02, …) and classify:

| Kind | Example | What we do |
|---|---|---|
| `clear` | Normal inquiry | Continue |
| `business` | Not found, locked, no servicing rights, insufficient funds | Stop with `business_outcome` and a code (exit 0) |
| `validation` | Empty / non-numeric member id | `failed` with expected vs observed |
| `recoverable` | Session timeout, system dialog | Click **Continue Session** or **Dismiss**, then resume |
| `takeover` | “Human approval required” popup | Stop automation; human Approve/Cancel |

**UI drift (secondary).** Locators are ordered fallbacks, not a single brittle CSS id. If every selector misses, the step fails with a screenshot and HTML dump — we do not silently click coordinates. Screen ids are extra checkpoints. We do not auto-heal the JSON; a miss means rediscover.

---

## 4. Heterogeneity & multi-tenant

**Two surfaces, one recording.** `WebSurface` talks to Playwright (URL + CSS). `DesktopSurface` talks to a Tk MEMSYS window (`open app`, no URL). Discover on web writes **both** locator families onto the same steps so desktop replay does not need a second recording. `--surface` chooses the adapter at run time.

**Multi-tenant (same vendor app).** Artifacts bind to `vendor`/`app`, not to a customer. `tenants/tenants.json` maps an id (`lab`, `cu-a`) to a **url** (fills `{base_url}`) and an **app** (fills `{app}`). Pass `--tenant lab`. Optional overlays can swap host or a locator without editing the base JSON. We do not re-record per institution.

---

## 5. Escalation & handoff

**Stuck** is detected as: locator timeout, unexpected approval overlay, policy-denied action, or a recoverable page we cannot clear. Discover also pauses when the LLM calls `ask_human`.

**Take control.** In a headed window, the operator presses **H** from the keyboard on the MEMSYS page". Automation stops. They finish the screen in that same browser/session.

**Hand back.** Press **Enter** on that page (or the on-page “return to automation” control). The run continues from the current DOM — we do not restart the browser. Approval popups are different: do **not** press H; automation already stopped; Approve or Cancel, then we classify the result.

The live session never forks. `control_owner` is either automation or human on the same Chrome/Tk window.

---

## 6. Safety

Guardrails are a **boring allowlist** (`policies/default.json`), plus credential rules:

- **Hosts / paths** — only localhost mock routes unless you change the file.

- **Actions** — navigate, click, type, a few inspect actions. Right-click, drag, `evaluate` are not on the list; the prompt is blocked before discover.

- **Headless discover is forbidden.**
- **Irreversible** (`Post New Share`) — headed runs ask a human; headless blocks.

- **Secrets** — operator id and password must not appear in the user prompt; rewrite is skipped. Evidence text is redacted. Replay always injects credentials from `.env`.

**Limits.** This is not a model-level safety stack. The LLM in discover can still try odd clicks *inside* the allowlist. We do not inspect prompt injection beyond credential literals. Policy is file-based, not per-tenant RBAC. Video/screenshots can still *show* a typed id on the sign-on field even when JSON is scrubbed.

---

## 7. Cuts

Left out on purpose: queues, a control plane, visual (x, y) production clicks, auto-healing locators, a hosted registry, a second vendor besides MEMSYS, parallel replay workers, and browsers other than Chromium.

**What we would build next**

- **LangSmith observability** — trace rewrite, each discover step, and replay outcomes (latency, tokens, which catalog JSON fired) so a grader or operator can see a run without opening raw JSONL.
- **Postgres** — persist runs, artifacts, evidence pointers, and tenant bindings instead of only `runs/` and `workflows/` files.
- **HTML report for replay** — a single page per replay: steps, screen ids, pass/fail, screenshot, and video, so non-engineers can review without the CLI.

- **Semantic rewrite cache (Redis)** — embed the user query; if Redis finds a **similar meaning**, skip discover and **replay** the existing artifact (today we only match after LLM rewrite to the exact canonical checklist).

- **Local LLM for query rewrite** — run the rewrite step on a local model (Ollama / similar) instead of a paid cloud LLM. Discover can still use a stronger hosted model when a new path must be recorded. That cuts rewrite cost on every prompt, including prompts that then hit catalog replay.

- **User-query test-case sheet** — a spreadsheet/CSV of goals (happy path, 99999, 77777, empty id, blocked right-click) with expected status codes, used in CI.

- **LLM evaluation (DeepEval)** — score only the model pieces (prompt rewrite and discover), not Playwright replay. A small suite would check: rewrite stays in action words with no secrets; catalog would choose replay vs discover correctly; discover’s structured outcome matches the page (`success` vs `MEMBER_NOT_FOUND`). Fail a CI job when faithfulness or format scores drop.

- **Jenkins for regression** — use a Jenkins pipeline as the next CI step: start the local mock and run **headless replay** of saved workflows (no LLM). Fail the build when a case does not match. Discover would stay off that pipeline (headed browser + API key).

- **Playwright parallel replay** — run several saved workflows at once (pytest-xdist or Playwright’s worker pool) so a full regression suite finishes faster than today’s one-browser-at-a-time CLI.

- **More browsers (Firefox, Edge)** — replay the same UserFlow JSON in Firefox and Microsoft Edge in addition to Chromium, so a recording is not locked to one engine.

- **Stronger guardrails (NeMo Guardrails)** — add a rail in front of rewrite/discover for jailbreaks, off-policy goals, and PII, on top of today’s host/action allowlist.
