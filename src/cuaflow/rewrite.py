"""Rewrite a user prompt into a stable checklist so wording changes still match."""

from __future__ import annotations

import re

from cua.enums import SurfaceKind
from pydantic import BaseModel, Field

from .discover.lookup import member_id_in_prompt, secrets_in_prompt
from .discover.userflow import SuccessCriteria, VerifyCheck
from .runtime import make_llm
from .settings import Settings

_CREDENTIAL_BLOCK = (
    "Do not put operator id or password in the prompt. "
    "They are read from .env. Say: enter operator id and password"
)
_SKIP_CREDENTIAL_TOKENS = {"and", "then", "to", "the", "from", "click", "button", "on", "a", "an"}
_OPERATOR_VALUE = re.compile(
    r"(?i)(?:operator\s+id|(?:log\s*in|sign(?:ed)?\s+on)\s+as)\s+(?:as|is|:)?\s*(?!\{operator\})(\S+)"
)
_PASSWORD_VALUE = re.compile(
    r"(?i)(?:enter\s+)?password\s+(?:as|is|:)?\s*(?!\{password\})(\S+)"
)

_STYLE = (
    "One short sentence per user action, in order. Reuse this wording when the action matches: "
    "Open base_url. Enter operator id {operator}. Enter password {password}. Click Sign On. "
    "Enter member id {member_id}. Click Search. Click Open Sub-Account. Verify the sub-account page opened. "
    "Click Cancel. Click Sign Off. "
    "Never write a real operator id or password. Always {operator} and {password}. "
    "If the user said enter operator id and password (no values), write "
    "Enter operator id {operator}. Enter password {password}. "
    "If the user said Sign On or Search without credentials, still include "
    "Enter operator id {operator}. Enter password {password}. Click Sign On. "
    "When the user says open app, write Open app. Never write base_url or an http URL for that step. "
    "When the user says open base_url, keep the words base_url. Never write an http URL. "
    "A path after it is allowed (Open base_url/main/inquiry). "
    "Keep every extra action the user asked for. Never drop, merge, or invent steps. "
    "A click is not a verification. Only add a Verify step / checks entry when the user said "
    "verify, confirm, ensure, or check that. Do not verify Sign Off unless they asked to. "
    "After Search, if the user asked to verify the member record (with or without saying found), "
    "set capture=true and leave must_see empty: existence is dynamic — record found or not found. "
    "Keep unusual actions in the user's wording (right click, double click, drag, hover, evaluate). "
    "Never rewrite those into a normal Click. "
)


class CanonicalTask(BaseModel):
    steps: list[str] = Field(
        min_length=1,
        description=(
            "Ordered atomic UI steps, one action each. Same actions must use identical wording. "
            f"{_STYLE} Use {{password}} never a real password. Use {{operator}} never a real operator id. "
            "Use {{member_id}} when a member id is present."
        ),
    )
    success: SuccessCriteria = Field(
        default_factory=SuccessCriteria,
        description=(
            "Verify only what the user asked to verify. checks is empty when they only asked to click. "
            "Do not put Sign Off or Cancel into must_see."
        ),
    )
    url: str | None = Field(default=None, description="First URL to open")
    operator_id: str | None = Field(default=None, description="Operator id if mentioned")
    member_id: str | None = Field(default=None, description="Member id if mentioned, digits only")

    @property
    def rewritten(self) -> str:
        """Join canonical steps into one period-separated checklist string."""
        parts: list[str] = []
        for step in self.steps:
            text = " ".join(step.split()).rstrip(".")
            if text:
                parts.append(text + ".")
        return " ".join(parts)

    def for_agent(self, *, settings=None, surface: str = SurfaceKind.WEB, tenant=None) -> str:
        """Expand base_url and member_id for the discover agent (not the stored title)."""
        from .settings import expand_base_url, load_settings

        text = self.rewritten
        if surface == SurfaceKind.DESKTOP:
            text = re.sub(
                r"open(?:\s+url)?\s+(?:\{)?base_url\}?\S*",
                "Open app",
                text,
                flags=re.I,
            )
        else:
            origin = tenant.origin() if tenant is not None else None
            text = expand_base_url(text, settings or load_settings(), origin=origin)
        if self.member_id:
            text = text.replace("{member_id}", self.member_id)
        checks = self.success.checks if self.success else []
        for check in checks:
            if check.capture:
                shown = check.description or "whether the member record was found"
                clause = (
                    f" After {check.after}, capture on that page: {shown}."
                    if check.after
                    else f" On the final page capture: {shown}."
                )
                text += clause + " Found and not found are both valid; record the page result and do not fail."
                continue
            shown = "; ".join(check.must_see) if check.must_see else check.description
            if check.after:
                text += f" After {check.after}, verify on that page: {shown}."
            elif shown:
                text += f" On the final page verify: {shown}."
        return text


class CredentialPromptBlocked(ValueError):
    """User prompt contains an operator id or password value; do not call the rewrite LLM."""


def credentials_in_prompt(prompt: str, settings: Settings) -> str | None:
    """Return a block reason if the prompt contains credential values (not placeholders)."""
    blob = prompt or ""
    operator = (settings.memsys_operator or "").strip()
    password = (settings.memsys_password or "").strip()
    if operator and re.search(rf"(?i)\b{re.escape(operator)}\b", blob):
        return _CREDENTIAL_BLOCK
    if password and len(password) >= 3 and re.search(rf"(?i)\b{re.escape(password)}\b", blob):
        return _CREDENTIAL_BLOCK
    for match in _OPERATOR_VALUE.finditer(blob):
        token = match.group(1).rstrip(".,;:/")
        if token.lower() not in _SKIP_CREDENTIAL_TOKENS and not token.startswith("{"):
            return _CREDENTIAL_BLOCK
    for match in _PASSWORD_VALUE.finditer(blob):
        token = match.group(1).rstrip(".,;:/")
        if token.lower() not in _SKIP_CREDENTIAL_TOKENS and not token.startswith("{"):
            return _CREDENTIAL_BLOCK
    return None


class PromptRewriter:
    def __init__(self, settings: Settings) -> None:
        """Bind rewrite to settings (API keys, operator, password)."""
        self.settings = settings

    async def rewrite(self, prompt: str) -> CanonicalTask:
        """Turn a user goal into CanonicalTask, or raise if credentials leaked."""
        reason = credentials_in_prompt(prompt, self.settings)
        if reason:
            raise CredentialPromptBlocked(reason)
        hidden = prompt.strip()
        operator = (self.settings.memsys_operator or "").strip()
        if operator:
            hidden = re.sub(re.escape(operator), "{operator}", hidden, flags=re.I)
        for secret in secrets_in_prompt(prompt, self.settings):
            hidden = hidden.replace(secret, "{password}")
        try:
            result = await self._invoke(hidden)
        except CredentialPromptBlocked:
            raise
        except Exception as exc:
            print(f"Rewrite LLM failed ({exc}); using original wording")
            result = CanonicalTask(steps=[hidden], success=SuccessCriteria())
        return stabilize(result, prompt, self.settings)

    async def _invoke(self, hidden: str) -> CanonicalTask:
        """Call the rewrite LLM and parse CanonicalTask JSON."""
        from browser_use.llm.messages import SystemMessage, UserMessage

        llm = make_llm(self.settings)
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "You turn a browser task into an ordered checklist of atomic UI steps "
                        "and optional verify checks. "
                        "If two requests mean the same actions in the same order, the steps must be identical. "
                        "If the user adds, removes, or changes an action, the step list must change. "
                        "checks: include an entry only when the user said verify/confirm/ensure/check that. "
                        "A member search result is capture=true with empty must_see — found and not found "
                        "are both valid outcomes to record, never a failed verify. "
                        "Click Sign Off, Click Cancel, Click Search are actions — not verifications. "
                        "Do not require the final page to still show an earlier screen after later clicks. "
                        f"{_STYLE}"
                    )
                ),
                UserMessage(content=hidden),
            ],
            output_format=CanonicalTask,
        )
        result = response.completion
        if isinstance(result, CanonicalTask):
            return result
        return CanonicalTask.model_validate(result)


def _scrub(text: str, member_id: str | None, secrets: dict[str, str], operator: str | None = None) -> str:
    """Replace secrets and member ids with placeholders in one string."""
    text = " ".join(text.split())
    if member_id:
        text = text.replace(member_id, "{member_id}")
    if operator:
        text = re.sub(re.escape(operator), "{operator}", text, flags=re.I)
    for secret, placeholder in secrets.items():
        text = text.replace(secret, placeholder)
    return text


def stabilize(task: CanonicalTask, prompt: str, settings: Settings) -> CanonicalTask:
    """Normalize LLM rewrite output: env sign-on, URLs, capture checks."""
    member_id = task.member_id or member_id_in_prompt(prompt) or member_id_in_prompt(" ".join(task.steps))
    secrets = secrets_in_prompt(prompt, settings)
    operator = (settings.memsys_operator or "").strip() or None
    steps = [_scrub(step, member_id, secrets, operator=operator) for step in (task.steps or [prompt]) if step.strip()]
    steps = [_fold_allowed_urls(step, settings) for step in steps]
    steps = [_fold_open_app(step, prompt) for step in steps]
    steps = _ensure_env_signon(steps, prompt)
    steps = _keep_blocked_actions(steps, prompt, settings)
    raw = task.success or SuccessCriteria()
    checks = [
        _stabilize_check(check, member_id, secrets, operator=operator)
        for check in raw.checks
    ]
    if _prompt_wants_member_record_capture(prompt) and not any(check.capture for check in checks):
        checks.append(
            VerifyCheck(
                after="Click Search",
                description="Capture whether the member record was found",
                capture=True,
            )
        )
    description = _scrub(raw.description, member_id, secrets, operator=operator) if raw.description else ""
    if checks and all(check.capture for check in checks):
        description = (
            "Capture whether member {member_id} was found"
            if member_id
            else "Capture whether the member record was found"
        )
    success = SuccessCriteria(
        description=description,
        checks=checks,
        must_see=[_scrub(item, member_id, secrets, operator=operator) for item in raw.must_see if item.strip()],
        must_not_see=[_scrub(item, member_id, secrets, operator=operator) for item in raw.must_not_see if item.strip()],
        screen_id=(raw.screen_id or "").strip() or None,
    )
    return CanonicalTask(
        steps=steps or [prompt.strip()],
        success=success,
        url=task.url,
        operator_id=None,
        member_id=member_id,
    )


def _prompt_wants_member_record_capture(prompt: str) -> bool:
    """True if the user asked to capture found vs not-found."""
    blob = (prompt or "").lower()
    if not re.search(r"\b(verify|confirm|ensure|check)\b", blob):
        return False
    if re.search(r"savings|balance|confirmation|sub-account|sub account", blob):
        return False
    return bool(re.search(r"member record|\bthe member\b", blob))


def _search_result_capture(check: VerifyCheck) -> bool:
    """Build a capture check after Search (do not fail on not-found)."""
    after = (check.after or "").lower()
    if "search" not in after:
        return False
    blob = " ".join([check.description, *check.must_see]).lower()
    return "found" in blob or "member record" in blob or blob.strip() in {"the member", "member"}


def _stabilize_check(
    check: VerifyCheck, member_id: str | None, secrets: dict[str, str], operator: str | None = None
) -> VerifyCheck:
    """Scrub one verify check and turn search-result verifies into capture."""
    capture = bool(check.capture) or _search_result_capture(check)
    description = _scrub(check.description, member_id, secrets, operator=operator)
    if capture:
        description = "Capture whether the member record was found"
    return VerifyCheck(
        after=_scrub(check.after, member_id, secrets, operator=operator),
        description=description,
        must_see=[]
        if capture
        else [_scrub(item, member_id, secrets, operator=operator) for item in check.must_see if item.strip()],
        must_not_see=[_scrub(item, member_id, secrets, operator=operator) for item in check.must_not_see if item.strip()],
        screen_id=(check.screen_id or "").strip() or None,
        capture=capture,
    )


_ENTER_OPERATOR_AND_PASSWORD = re.compile(
    r"(?i)enter\s+operator(?:\s+id)?(?:\s*,)?\s+and\s+(?:enter\s+)?password"
)


def _ensure_env_signon(steps: list[str], prompt: str) -> list[str]:
    """Credentials come from .env. Insert {operator}/{password} when the task signs on or searches."""
    if not re.search(r"sign\s*on|log\s*in|\bsearch\b|\bmember\b|operator", prompt or "", re.I):
        return steps
    out: list[str] = []
    for step in steps:
        if _ENTER_OPERATOR_AND_PASSWORD.search(step):
            rest = _ENTER_OPERATOR_AND_PASSWORD.sub(" ", step)
            rest = re.sub(r"\s+", " ", rest).strip(" ,.")
            out.append("Enter operator id {operator}")
            out.append("Enter password {password}")
            if rest:
                out.append(rest)
            continue
        text = re.sub(
            r"(?i)enter operator(?:\s+id)?(?!\s*\{operator\})(?:\s+\S+)?",
            "Enter operator id {operator}",
            step,
        )
        text = re.sub(
            r"(?i)enter password(?!\s*\{password\})(?:\s+\S+)?",
            "Enter password {password}",
            text,
        )
        out.append(text)
    blob = " ".join(out).lower()
    needed: list[str] = []
    if "operator" not in blob:
        needed.append("Enter operator id {operator}")
    if "password" not in blob:
        needed.append("Enter password {password}")
    if needed:
        signon_at = next((i for i, item in enumerate(out) if re.search(r"sign\s*on", item, re.I)), None)
        idx = signon_at if signon_at is not None else (1 if out and re.match(r"open\b", out[0], re.I) else 0)
        out = out[:idx] + needed + out[idx:]
    blob = " ".join(out).lower()
    if "sign on" not in blob:
        pw_at = next((i for i, item in enumerate(out) if "password" in item.lower()), 0)
        out = out[: pw_at + 1] + ["Click Sign On"] + out[pw_at + 1 :]
    return out


def _fold_open_app(text: str, prompt: str) -> str:
    """Desktop wording is Open app — never base_url or a host."""
    if not re.search(r"\bopen\s+app\b", prompt or "", re.I):
        return text
    out = re.sub(
        r"^open(?:\s+the)?(?:\s+memsys)?(?:\s+desktop)?\s+app\b.*",
        "Open app",
        text,
        flags=re.I,
    )
    return re.sub(
        r"^open(?:\s+url)?\s+(?:\{)?base_url\}?\S*",
        "Open app",
        out,
        flags=re.I,
    )


def _fold_allowed_urls(text: str, settings: Settings) -> str:
    """Keep hosts that policy blocks; rewrite the .env origin as the words base_url."""
    from .safety.policy import load_policy, urls_in
    from .settings import prompt_token_from_url

    policy = load_policy(settings.cua_policy_path)
    out = text
    for url in urls_in(text):
        if policy.check_url(url):
            continue
        out = out.replace(url, prompt_token_from_url(url, settings))
    return out


def _keep_blocked_actions(steps: list[str], prompt: str, settings: Settings) -> list[str]:
    """If the user asked for a blocked host, path, or action, keep it in the checklist."""
    from .safety.policy import load_policy, urls_in

    policy = load_policy(settings.cua_policy_path)
    blob = " ".join(steps)
    extra: list[str] = []
    kept_kinds = {kind for kind, _label in policy.blocked_prompt_actions(blob)}
    for _kind, label in policy.blocked_prompt_actions(prompt):
        if _kind not in kept_kinds:
            extra.append(f"{label} is blocked by policy")
    kept_urls = set(urls_in(blob))
    for url in urls_in(prompt):
        if policy.check_url(url) and url not in kept_urls:
            extra.append(f"Open {url} is blocked by policy")
    return list(steps) + extra if extra else steps
