"""Human-in-the-loop during discover: pause on any unexpected UI, any screen, any time."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

from cua.enums import ButtonLabel, Driver

from ..orchestrator.orchestrator import RunContext

_HINTS = (
    "unexpected confirmation",
    "human approval required",
    "verify you are human",
    "are you a robot",
    "access denied",
    "captcha",
    "system message",
)

_OVERLAY_JS = """() => {
  const sel = '.overlay, [role="dialog"], .modal, #sysmsg, #approve, .warn';
  const nodes = Array.from(document.querySelectorAll(sel));
  const shown = nodes.filter((el) => {
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 8 && r.height > 8;
  });
  if (!shown.length) return '';
  return shown.map((el) => (el.innerText || '').trim()).filter(Boolean).join(' | ').slice(0, 400);
}"""

HITL_TASK = (
    " On ANY screen, if something unexpected appears (popup, overlay, extra dialog, "
    "permission prompt, unknown page, or you are stuck), call ask_human and wait. "
    "After a human continues, proceed from the current page. You may need to do this more than once. "
    "If the human clicks Cancel or does not Approve the sub-account popup, stop. Do not continue."
)

_ARM_JS = """() => {
  const KEY = 'cuaflowHitl';
  let flag = '';
  try { flag = sessionStorage.getItem(KEY) || ''; } catch (e) {}
  if (flag === 'resume' || flag === 'abort') return flag;
  if (window.__cuaflowHitl === 'resume' || window.__cuaflowHitl === 'abort') return window.__cuaflowHitl;
  window.__cuaflowHitlActive = true;
  try { window.top.__cuaflowHitlActive = true; } catch (e) {}
  if (!window.__cuaflowHitlArmAt) window.__cuaflowHitlArmAt = Date.now();
  const ID = 'cuaflow-hitl-bar';
  if (document.getElementById(ID)) return 'armed';
  const set = (mode) => {
    window.__cuaflowHitl = mode;
    try { sessionStorage.setItem(KEY, mode); } catch (e) {}
  };
  const bar = document.createElement('div');
  bar.id = ID;
  bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#660000;color:#fff;font:14px Tahoma,sans-serif;padding:10px 14px;text-align:center;';
  bar.innerHTML = 'Automation is paused. Finish this screen first (Approve / dismiss), then press <b>Enter</b> to give the browser back.';
  const ret = document.createElement('button');
  ret.type = 'button';
  ret.textContent = 'Return to automation';
  ret.style.cssText = 'margin-left:12px;font:13px Tahoma;padding:2px 10px;cursor:pointer;';
  ret.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); set('resume'); });
  bar.appendChild(ret);
  (document.body || document.documentElement).appendChild(bar);
  if (!window.__cuaflowHitlBound) {
    window.__cuaflowHitlBound = true;
    window.addEventListener('keydown', (e) => {
      if (!window.__cuaflowHitlActive || e.key !== 'Enter' || e.repeat) return;
      if (!window.__cuaflowHitlArmAt) window.__cuaflowHitlArmAt = Date.now();
      if (Date.now() - window.__cuaflowHitlArmAt < 800) return;
      const el = e.target;
      const tag = ((el && el.tagName) || '').toLowerCase();
      if (tag === 'textarea' || tag === 'select' || tag === 'input' || tag === 'button') return;
      e.preventDefault();
      e.stopPropagation();
      set('resume');
    }, true);
  }
  return 'armed';
}"""

_CLEAR_JS = """() => {
  window.__cuaflowHitlActive = false;
  window.__cuaflowHitl = '';
  window.__cuaflowHitlArmAt = 0;
  window.__cuaflowTakeover = '';
  try { window.top.__cuaflowHitlActive = false; } catch (e) {}
  try { window.top.__cuaflowTakeover = ''; } catch (e) {}
  try { sessionStorage.removeItem('cuaflowHitl'); } catch (e) {}
  const el = document.getElementById('cuaflow-hitl-bar');
  if (el) el.remove();
  return 'cleared';
}"""

_RESET_FLAG_JS = """() => {
  window.__cuaflowHitl = '';
  try { sessionStorage.removeItem('cuaflowHitl'); } catch (e) {}
  return 'armed';
}"""

_TAKEOVER_ARM_JS = """() => {
  const paused = () => {
    try { if (window.top && window.top.__cuaflowHitlActive) return true; } catch (e) {}
    return !!window.__cuaflowHitlActive;
  };
  const flagged = () => {
    try { if (window.top && window.top.__cuaflowTakeover === '1') return true; } catch (e) {}
    return window.__cuaflowTakeover === '1';
  };
  if (paused()) return 'armed';
  if (flagged()) return 'takeover';
  if (window.__cuaflowTakeoverBound) return 'armed';
  window.__cuaflowTakeoverBound = true;
  window.addEventListener('keydown', (e) => {
    if (e.repeat || e.ctrlKey || e.altKey || e.metaKey) return;
    if (e.key !== 'h' && e.key !== 'H') return;
    if (paused()) return;
    e.preventDefault();
    e.stopPropagation();
    window.__cuaflowTakeover = '1';
    try { window.top.__cuaflowTakeover = '1'; } catch (err) {}
  }, true);
  const ID = 'cuaflow-takeover-hint';
  if (!document.getElementById(ID) && document.body) {
    const bar = document.createElement('div');
    bar.id = ID;
    bar.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:2147483646;background:#003366;color:#fff;font:12px Tahoma,sans-serif;padding:6px 10px;text-align:center;';
    bar.textContent = 'Press H in this window to take control. After you finish, press Enter here to resume.';
    document.body.appendChild(bar);
  }
  return 'armed';
}"""

_TAKEOVER_CLEAR_JS = """() => {
  window.__cuaflowTakeover = '';
  try { window.top.__cuaflowTakeover = ''; } catch (e) {}
  return 'cleared';
}"""

_HUMAN_ARM_JS = """() => {
  const chrome = (el) => {
    if (!el || !el.closest) return true;
    return !!(el.closest('#cuaflow-hitl-bar') || el.closest('#cuaflow-takeover-hint'));
  };
  const store = () => {
    try {
      const top = window.top;
      top.__cuaflowHumanLog = top.__cuaflowHumanLog || [];
      return top.__cuaflowHumanLog;
    } catch (e) {
      window.__cuaflowHumanLog = window.__cuaflowHumanLog || [];
      return window.__cuaflowHumanLog;
    }
  };
  const hitlOn = () => {
    try { if (window.top && window.top.__cuaflowHitlActive) return true; } catch (e) {}
    return !!window.__cuaflowHitlActive;
  };
  const push = (item) => {
    item.actor = hitlOn() ? 'human' : 'automation';
    store().push(item);
  };
  const secret = (el) => {
    const type = ((el && el.type) || '').toLowerCase();
    const name = ((el && (el.name || el.id)) || '');
    return type === 'password' || /pass|pwd|token|secret|^opr$|operator/i.test(name);
  };
  const fieldName = (el) => {
    if (!el) return '';
    return String(el.getAttribute('name') || el.id || el.getAttribute('aria-label') || '').trim();
  };
  const labelOf = (el) => {
    if (!el) return '';
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'button') {
      const val = String(el.getAttribute('value') || el.textContent || '').trim();
      if (val) return val.replace(/\\s+/g, ' ').slice(0, 80);
    }
    if (tag === 'a') {
      const t = String(el.innerText || el.textContent || '').trim();
      if (t) return t.replace(/\\s+/g, ' ').slice(0, 80);
    }
    const text = String(el.getAttribute('value') || el.innerText || el.getAttribute('aria-label') || el.getAttribute('name') || el.id || el.tagName || '').trim();
    return text.replace(/\\s+/g, ' ').slice(0, 80);
  };
  const clickEl = (e) => {
    const path = (typeof e.composedPath === 'function') ? e.composedPath() : [];
    const nodes = path.length ? path : [e.target];
    for (const node of nodes) {
      if (!node || !node.tagName) continue;
      const tag = node.tagName.toLowerCase();
      const type = String(node.type || '').toLowerCase();
      if (tag === 'button' || tag === 'a' || (tag === 'input' && /submit|button|image|reset/.test(type))) return node;
    }
    return e.target;
  };
  if (window.__cuaflowHumanLogDoc !== document) window.__cuaflowHumanLogBound = false;
  if (window.__cuaflowHumanLogBound) return 'armed';
  window.__cuaflowHumanLogBound = true;
  window.__cuaflowHumanLogDoc = document;
  document.addEventListener('click', (e) => {
    const el = clickEl(e);
    if (chrome(el)) return;
    const label = labelOf(el);
    if (!label) return;
    push({kind: 'click', label: label});
  }, true);
  document.addEventListener('change', (e) => {
    const el = e.target;
    if (chrome(el)) return;
    push({
      kind: 'change',
      label: labelOf(el) || fieldName(el),
      value: secret(el) ? '«redacted»' : String((el && el.value) || '').slice(0, 80),
    });
  }, true);
  document.addEventListener('keydown', (e) => {
    if (!e || e.repeat) return;
    const el = e.target;
    if (chrome(el)) return;
    const tag = ((el && el.tagName) || '').toLowerCase();
    const inField = tag === 'input' || tag === 'textarea' || tag === 'select';
    let hitl = !!window.__cuaflowHitlActive;
    try { hitl = hitl || !!(window.top && window.top.__cuaflowHitlActive); } catch (err) {}
    if (e.key === 'Enter' && !inField && hitl) return;
    if ((e.key === 'h' || e.key === 'H') && !inField) return;
    const name = fieldName(el) || labelOf(el);
    if (secret(el)) {
      push({kind: 'key', key: '«redacted»', label: name || 'password'});
      return;
    }
    push({kind: 'key', key: String(e.key || '').slice(0, 24), label: name});
  }, true);
  document.addEventListener('submit', (e) => {
    const who = e.submitter;
    if (chrome(who || e.target)) return;
    if (who) push({kind: 'click', label: labelOf(who)});
  }, true);
  return 'armed';
}"""

_HUMAN_DRAIN_JS = """() => {
  const root = (() => {
    try {
      if (window.top && Array.isArray(window.top.__cuaflowHumanLog)) return window.top;
    } catch (e) {}
    window.__cuaflowHumanLog = window.__cuaflowHumanLog || [];
    return window;
  })();
  const log = root.__cuaflowHumanLog || [];
  const at = root.__cuaflowHumanDrainAt || 0;
  const slice = log.slice(at);
  root.__cuaflowHumanDrainAt = log.length;
  return JSON.stringify(slice);
}"""


def live_view_url(agent: Any) -> str:
    """CDP/devtools URL for the live browser, if we can find one."""
    session = getattr(agent, "browser_session", None) or getattr(agent, "browser", None)
    for attr in ("cdp_url", "browser_url", "live_url"):
        value = getattr(session, attr, None)
        if isinstance(value, str) and value:
            return value
    return "headed browser window (this machine)"


def interruption_reason(*, overlay: str = "", page_text: str = "") -> str | None:
    """Human-readable reason stored on the current intervention."""
    overlay = (overlay or "").strip()
    if overlay:
        return f"blocking UI on this screen: {overlay[:120]}"
    blob = f"{overlay} {page_text}".lower()
    for hint in _HINTS:
        if hint in blob:
            return f"unexpected UI: {hint}"
    return None


def is_approval_overlay(overlay: str) -> bool:
    """True if the page is the MEMSYS human-approval popup."""
    blob = (overlay or "").lower()
    return "human approval required" in blob or "verify you are human" in blob


def poll_takeover_key() -> bool:
    """True if the operator pressed H (or the overlay takeover flag)."""
    if sys.platform != "win32":
        return False
    import msvcrt

    hit = False
    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch.lower() == "h":
            hit = True
    return hit


async def poll_takeover_page(agent: Any) -> bool:
    """H in the live browser (including iframes). Consumes the key so it is not typed into a field."""
    if agent is None:
        return False
    results = await _eval_targets(agent, _TAKEOVER_ARM_JS)
    if "takeover" not in results:
        return False
    await _eval_targets(agent, _TAKEOVER_CLEAR_JS)
    return True


async def discard_takeover(agent: Any) -> None:
    """Drop a leftover H flag so resume does not immediately pause again."""
    if agent is None:
        return
    await _eval_targets(agent, _TAKEOVER_CLEAR_JS)
    poll_takeover_key()


async def read_overlay(agent: Any) -> str:
    """Read HITL overlay state (H pressed, Enter to resume) from the page."""
    bits: list[str] = []
    for target in await _page_targets(agent):
        try:
            text = str(await target.evaluate(_OVERLAY_JS) or "").strip()
        except Exception:
            continue
        if text:
            bits.append(text)
    return " | ".join(bits)


async def _page_targets(agent: Any) -> list[Any]:
    """Playwright page plus frames to search for the overlay."""
    session = getattr(agent, "browser_session", None)
    if session is None:
        return []
    try:
        page = await session.get_current_page()
    except Exception:
        return []
    if page is None:
        return []
    targets = [page]
    try:
        frames = list(getattr(page, "frames", None) or [])
    except Exception:
        frames = []
    for frame in frames:
        if frame is not page and frame not in targets:
            targets.append(frame)
    return targets


async def _eval_targets(agent: Any, script: str) -> list[str]:
    """Run a JS snippet on each frame until one returns a value."""
    found: list[str] = []
    for target in await _page_targets(agent):
        try:
            found.append(str(await target.evaluate(script) or ""))
        except Exception:
            continue
    return found


_NOISE_CLICKS = frozenset({"HTML", "BODY", "DIV", "TD", "TR", "TABLE", "FONT", "CENTER", "SPAN", "P"})


def _actor_of(item: dict[str, Any]) -> str:
    """Tag a DOM event as human or automation."""
    raw = str(item.get("actor") or Driver.HUMAN).strip().lower()
    if raw == Driver.AUTOMATION:
        return Driver.AUTOMATION
    return Driver.HUMAN


def describe_page_event(item: dict[str, Any]) -> tuple[str, str]:
    """Split a DOM event into (actor, action text) for the run log."""
    actor = _actor_of(item)
    kind = str(item.get("kind") or "act")
    label = " ".join(str(item.get("label") or "").split())
    if kind == "change":
        value = item.get("value")
        body = f"change {label} = {value}".strip() if value else f"change {label}".strip()
        return actor, body
    if kind == "click":
        return actor, f"click {label}".strip()
    if kind == "type":
        value = str(item.get("value") or "")
        if label:
            return actor, f"type {value} in {label}".strip()
        return actor, f"type {value}".strip()
    if kind == "key":
        key = str(item.get("key") or "")
        if label:
            return actor, f"key {key} in {label}".strip()
        return actor, f"key {key}".strip()
    return actor, f"{kind} {label}".strip()


def describe_human_event(item: dict[str, Any]) -> str:
    """Turn a DOM event into a tagged log line (human … / automation …)."""
    actor, body = describe_page_event(item)
    return f"{actor} {body}".strip()


def collapse_human_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive printable keydowns in the same field and actor into one type event."""
    out: list[dict[str, Any]] = []
    buf: dict[str, str] | None = None

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        text = buf["chars"]
        label = buf["label"]
        if text:
            item: dict[str, Any] = {"kind": "type", "label": label, "value": text}
            if buf.get("actor"):
                item["actor"] = buf["actor"]
            out.append(item)
        buf = None

    for item in items:
        kind = str(item.get("kind") or "")
        if kind == "key":
            key = str(item.get("key") or "")
            label = str(item.get("label") or "")
            actor = str(item.get("actor") or "")
            if key == "«redacted»":
                flush()
                out.append(item)
                continue
            if len(key) == 1 and key.isprintable():
                if buf and buf["label"] == label and buf.get("actor") == actor:
                    buf["chars"] += key
                else:
                    flush()
                    buf = {"label": label, "chars": key, "actor": actor}
                continue
            flush()
            out.append(item)
            continue
        flush()
        out.append(item)
    flush()
    return out


def _page_acts(holder: dict[str, Any]) -> list[str]:
    """Durable action list for this run. Never replaced with a new empty list."""
    acts = holder.setdefault("page_acts", [])
    holder["human_acts"] = acts
    return acts


def _append_page_note(acts: list[str], note: str, ctx: Any = None) -> None:
    """Keep a tagged action in the run log and print it."""
    if not note or note in {f"{Driver.HUMAN} click", f"{Driver.AUTOMATION} click"}:
        return
    if acts and acts[-1] == note:
        return
    acts.append(note)
    actor, _, body = note.partition(" ")
    if ctx is not None:
        ctx.log("page_action", actor=actor, what=body)
        return
    print(f"[{actor}] {body}")


async def drain_human_log(agent: Any, holder: dict[str, Any]) -> None:
    """Copy new page actions into the run log. The in-page log is not deleted."""
    acts = _page_acts(holder)
    ctx = holder.get("ctx")
    batch: list[dict[str, Any]] = []
    for raw in await _eval_targets(agent, _HUMAN_DRAIN_JS):
        if not raw or raw[0] not in "[{":
            continue
        try:
            items = json.loads(raw)
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "")
            label = " ".join(str(item.get("label") or "").split())
            if kind == "click" and label.upper() in _NOISE_CLICKS:
                continue
            batch.append(item)
    for item in collapse_human_items(batch):
        _append_page_note(acts, describe_human_event(item), ctx if ctx is not None and hasattr(ctx, "log") else None)


async def capture_page_actions(agent: Any, holder: dict[str, Any]) -> None:
    """Bind click/key listeners on every frame and flush new actions into the run log."""
    if agent is None:
        return
    await _eval_targets(agent, _HUMAN_ARM_JS)
    await drain_human_log(agent, holder)


def approval_decision(
    acts: list[str],
    *,
    overlay: str = "",
    urls: str = "",
    saw_approval: bool = False,
) -> str | None:
    """Return approve or cancel from human clicks and the page after the overlay."""
    last: str | None = None
    for act in acts:
        low = act.lower()
        if f"click {ButtonLabel.CANCEL.lower()}" in low:
            last = "cancel"
        elif f"click {ButtonLabel.APPROVE.lower()}" in low:
            last = "approve"
    if last:
        return last
    if not saw_approval or is_approval_overlay(overlay):
        return None
    blob = (urls or "").lower()
    if "/subacct/confirm" in blob or "/subacct/done" in blob:
        return "approve"
    if "/main/member" in blob or "/main/inquiry" in blob or "/main/subacct" in blob:
        return "cancel"
    return None


async def read_page_urls(agent: Any) -> str:
    """Join page and iframe URLs so we can tell Approve (confirm) from Cancel (form)."""
    bits: list[str] = []
    for target in await _page_targets(agent):
        url = getattr(target, "url", None)
        if isinstance(url, str) and url.strip():
            bits.append(url.strip())
    return " ".join(bits)


async def wait_for_page_enter(agent: Any, holder: dict[str, Any]) -> str:
    """Resume after Approve, or after Cancel — then automation closes if not approved."""
    _page_acts(holder)
    await _eval_targets(agent, _CLEAR_JS)
    await discard_takeover(agent)
    await _eval_targets(agent, _ARM_JS)
    await capture_page_actions(agent, holder)
    overlay0 = await read_overlay(agent)
    saw_approval = bool(holder.get("approval_handoff")) or is_approval_overlay(overlay0)

    async def _handoff_done(mode: str) -> str:
        await drain_human_log(agent, holder)
        await _eval_targets(agent, _CLEAR_JS)
        await discard_takeover(agent)
        return mode

    while not holder.get("stop"):
        await capture_page_actions(agent, holder)
        overlay = await read_overlay(agent)
        urls = await read_page_urls(agent)
        decision = approval_decision(
            _page_acts(holder),
            overlay=overlay,
            urls=urls,
            saw_approval=saw_approval,
        )
        if decision:
            holder["approval_decision"] = decision
            return await _handoff_done("continue_after")
        results = await _eval_targets(agent, _ARM_JS)
        if "abort" in results:
            if saw_approval:
                holder["approval_decision"] = holder.get("approval_decision") or "cancel"
            return await _handoff_done("abort")
        if "resume" in results:
            overlay = await read_overlay(agent)
            if interruption_reason(overlay=overlay):
                await _eval_targets(agent, _RESET_FLAG_JS)
                await asyncio.sleep(0.15)
                continue
            if saw_approval and not holder.get("approval_decision"):
                holder["approval_decision"] = approval_decision(
                    _page_acts(holder),
                    overlay=overlay,
                    urls=await read_page_urls(agent),
                    saw_approval=True,
                ) or "cancel"
            return await _handoff_done("continue_after")
        await asyncio.sleep(0.15)
    if saw_approval:
        holder["approval_decision"] = holder.get("approval_decision") or "cancel"
    return await _handoff_done("abort")


def pause_agent(agent: Any) -> None:
    """Pause the Browser Use agent so a human can drive."""
    state = getattr(agent, "state", None)
    event = getattr(agent, "_external_pause_event", None)
    if state is not None:
        state.paused = True
    if event is not None:
        event.clear()
        return
    try:
        agent.pause()
    except Exception:
        pass


def stop_agent(agent: Any) -> None:
    """Stop the Browser Use agent so discover can close after Cancel."""
    if agent is None:
        return
    try:
        agent.stop()
    except Exception:
        pass
    pause_agent(agent)


def resume_agent(agent: Any) -> None:
    """Resume the agent after the human returns the page."""
    try:
        agent.resume()
    except Exception:
        pass


async def handoff(ctx: RunContext, holder: dict[str, Any], reason: str) -> str:
    """Pause automation, wait for the human, then resume the same session."""
    if holder.get("in_handoff"):
        return "continue_after"
    holder["in_handoff"] = True
    agent = holder.get("agent")
    overlay = await read_overlay(agent) if agent else ""
    holder["approval_handoff"] = is_approval_overlay(overlay)
    view = live_view_url(agent) if agent else "headed browser window (this machine)"
    try:
        if agent is not None:
            pause_agent(agent)
        ctx.log("human_handoff", reason=reason, live_view_url=view)
        holder["ctx"] = ctx
        holder["iv_from"] = len(_page_acts(holder))
        mode = await ctx.await_human(
            reason,
            live_view_url=view,
            wait_resume=lambda: wait_for_page_enter(agent, holder),
            human_actions=lambda: [
                item
                for item in _page_acts(holder)[int(holder.get("iv_from") or 0) :]
                if str(item).startswith(f"{Driver.HUMAN} ")
            ],
        )
        if agent is not None:
            resume_agent(agent)
        return mode
    finally:
        holder["in_handoff"] = False
        holder["takeover"] = False
        holder["ignore_takeover_until"] = time.monotonic() + 1.5
        agent = holder.get("agent")
        try:
            await discard_takeover(agent)
        except Exception:
            pass
        if overlay:
            holder["cleared_sig"] = overlay[:160]


def hitl_tools(ctx: RunContext, holder: dict[str, Any]):
    """Browser Use tools: ask_human so the LLM can request a takeover."""
    from browser_use import ActionResult, Tools

    tools = Tools()

    @tools.action(
        "Call this on ANY screen when something unexpected appears and a human must use the live browser. "
        "Use it as often as needed. Do not call this for normal planned steps."
    )
    async def ask_human(question: str) -> ActionResult:
        """LLM tool: pause and wait for the operator, then continue."""
        mode = await handoff(ctx, holder, question)
        if holder.get("approval_decision") == "cancel":
            stop_agent(holder.get("agent"))
            return ActionResult(
                extracted_content="Operator cancelled sub-account approval. Automation will close this run.",
                success=True,
                is_done=True,
                long_term_memory="Human cancelled approval. Do not continue.",
            )
        if mode == "abort":
            return ActionResult(
                extracted_content="Human aborted the discover run.",
                success=False,
                long_term_memory="Human aborted.",
            )
        return ActionResult(
            extracted_content=(
                "Human finished. Look at the CURRENT page and continue the remaining original steps. "
                "Do not repeat the click that opened the interruption."
            ),
            long_term_memory="Human cleared the interruption. Continue from the current screen.",
        )

    return tools


async def maybe_handoff_from_page(ctx: RunContext, holder: dict[str, Any], agent: Any) -> None:
    """If H was pressed or an approval overlay is up, hand off."""
    holder["ctx"] = ctx
    try:
        await capture_page_actions(agent, holder)
    except Exception:
        pass
    if holder.get("in_handoff"):
        return
    overlay = await read_overlay(agent)
    page_text = ""
    try:
        session = getattr(agent, "browser_session", None)
        if session is not None:
            summary = await session.get_browser_state_summary()
            page_text = " ".join(
                str(part)
                for part in (
                    getattr(summary, "title", ""),
                    getattr(summary, "page_text", "") or getattr(summary, "dom_text", ""),
                )
                if part
            )
    except Exception:
        pass
    reason = interruption_reason(overlay=overlay, page_text=page_text)
    if not reason:
        holder.pop("cleared_sig", None)
        return
    sig = (overlay or reason)[:160]
    if holder.get("cleared_sig") == sig:
        return
    await handoff(ctx, holder, reason)
    holder["cleared_sig"] = sig
    if holder.get("approval_decision") == "cancel":
        stop_agent(agent)
        holder["stop"] = True


def hitl_step_hook(ctx: RunContext, holder: dict[str, Any]):
    """Return an on-step callback that polls for human takeover."""
    async def on_step(agent: Any) -> None:
        """Before/after an agent step, check for H or an approval popup."""
        await maybe_handoff_from_page(ctx, holder, agent)

    return on_step


async def watch_operator_key(ctx: RunContext, holder: dict[str, Any]) -> None:
    """Let the operator take the browser at any moment by pressing H on the page (or in this terminal)."""
    holder["ctx"] = ctx
    while not holder.get("stop"):
        await asyncio.sleep(0.15)
        try:
            await capture_page_actions(holder.get("agent"), holder)
        except Exception:
            pass
        if holder.get("in_handoff") or holder.get("takeover"):
            continue
        until = float(holder.get("ignore_takeover_until") or 0)
        if until and time.monotonic() < until:
            try:
                await discard_takeover(holder.get("agent"))
            except Exception:
                pass
            continue
        page_hit = False
        try:
            page_hit = await poll_takeover_page(holder.get("agent"))
        except Exception:
            page_hit = False
        if not page_hit and not poll_takeover_key():
            continue
        print("H pressed — handing the browser to you. Finish the page, then press Enter there.")
        if holder.get("defer_takeover"):
            holder["takeover"] = True
            continue
        await handoff(ctx, holder, "operator took control")


class PageSession:
    """Expose a Playwright page through the same get_current_page() HITL uses."""

    def __init__(self, page: Any) -> None:
        """Wrap a Playwright page so HITL can treat it like a browser-use session."""
        self._page = page
        self.browser_session = self

    async def get_current_page(self) -> Any:
        """Return the live Playwright page."""
        return self._page
