"""Hardcoded domain values. Import these instead of repeating string literals.

Credentials still come from .env (Settings). This module holds names, codes,
paths, and labels that the mock MEMSYS app and cuaflow must agree on.
"""

from __future__ import annotations

from enum import StrEnum


class SurfaceKind(StrEnum):
    """Which UI adapter runs the same UserFlow JSON."""

    WEB = "web"
    DESKTOP = "desktop"


class RunKind(StrEnum):
    """What a Run is doing."""

    DISCOVER = "discover"
    REPLAY = "replay"


class AppId(StrEnum):
    """Vendor app ids stored on UserFlow and tenant maps."""

    MEMSYS = "memsys"
    DESKTOP_MEMSYS = "desktop:memsys"


class ScreenId(StrEnum):
    """MEMSYS screen codes shown in the chrome and stored on checkpoints."""

    SIGNON = "SON01"
    INQUIRY = "INQ01"
    MEMBER = "MEM02"
    SUBACCT = "SUB03"
    SUBACCT_CONFIRM = "SUB04"
    SUBACCT_DONE = "SUB05"
    SESSION_TIMEOUT = "SES99"
    PERMISSION_DENIED = "SEC01"
    MEMBER_LOCKED = "LCK01"


class OutcomeCode(StrEnum):
    """Stable codes for business outcomes and recoverable page states."""

    MEMBER_NOT_FOUND = "MEMBER_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    MEMBER_LOCKED = "MEMBER_LOCKED"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    VALIDATION_MEMBER_ID = "VALIDATION_MEMBER_ID"
    VALIDATION_PRODUCT = "VALIDATION_PRODUCT"
    VALIDATION_AMOUNT = "VALIDATION_AMOUNT"
    HUMAN_TAKEOVER = "HUMAN_TAKEOVER"
    SESSION_TIMEOUT = "SESSION_TIMEOUT"
    SYSTEM_DIALOG = "SYSTEM_DIALOG"


class ConditionKind(StrEnum):
    """How classify_runtime labels the current page."""

    CLEAR = "clear"
    BUSINESS = "business"
    VALIDATION = "validation"
    RECOVERABLE = "recoverable"
    TAKEOVER = "takeover"


class ResultStatus(StrEnum):
    """Final status returned to the caller."""

    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    FAILED = "failed"


class RunState(StrEnum):
    """Lifecycle of one discover or replay run."""

    CREATED = "CREATED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    HUMAN_CONTROL = "HUMAN_CONTROL"
    SUCCEEDED = "SUCCEEDED"
    BUSINESS_OUTCOME = "BUSINESS_OUTCOME"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class InterventionStatus(StrEnum):
    """Human-handoff request state."""

    OPEN = "open"
    CLAIMED = "claimed"
    RESOLVED = "resolved"


class ResumeMode(StrEnum):
    """What to do after a human hands the session back."""

    RETRY_STEP = "retry_step"
    CONTINUE_AFTER = "continue_after"
    ABORT = "abort"


class Driver(StrEnum):
    """Who holds the live session, and who performed a recorded page action."""

    NONE = "none"
    AUTOMATION = "automation"
    HUMAN = "human"


class StepType(StrEnum):
    """Recorder step kinds stored on UserFlow (and allowed by policy)."""

    NAVIGATE = "navigate"
    CLICK = "click"
    CHANGE = "change"
    KEY_DOWN = "keyDown"


class ActionKind(StrEnum):
    """Policy action kinds, including inspect-only and blocked gestures."""

    NAVIGATE = "navigate"
    CLICK = "click"
    CHANGE = "change"
    KEY_DOWN = "keyDown"
    WAIT = "wait"
    SCROLL = "scroll"
    EXTRACT = "extract"
    SCREENSHOT = "screenshot"
    DONE = "done"
    ASK_HUMAN = "ask_human"
    CONTEXT_CLICK = "contextclick"
    DOUBLE_CLICK = "doubleclick"
    DRAG = "drag"
    HOVER = "hover"
    EVALUATE = "evaluate"


class InputName(StrEnum):
    """Capability input names written into workflow JSON."""

    OPERATOR = "operator"
    PASSWORD = "password"
    MEMBER_ID = "member_id"
    PRODUCT = "product"
    OPENING_DEPOSIT = "opening_deposit"
    APP = "app"
    BASE_URL = "base_url"


class Placeholder(StrEnum):
    """Tokens stored in UserFlow values; filled at replay from .env or --set."""

    BASE_URL = "{base_url}"
    OPERATOR = "{operator}"
    PASSWORD = "{password}"
    MEMBER_ID = "{member_id}"
    APP = "{app}"


class RecordedUrl(StrEnum):
    """Canonical start targets written into UserFlow navigate steps."""

    SIGNON = "{base_url}/signon"
    APP = "{app}"


class UrlPath(StrEnum):
    """Paths on the mock MEMSYS origin."""

    ROOT = "/"
    SIGNON = "/signon"
    CONSOLE = "/console"
    NAV = "/nav"
    HEALTH = "/health"
    LAB = "/lab"
    LAB_RESET = "/lab/reset"
    MAIN = "/main"
    INQUIRY = "/main/inquiry"
    MEMBER = "/main/member"
    SUBACCT = "/main/subacct"
    SUBACCT_CONFIRM = "/main/subacct/confirm"
    SUBACCT_DONE = "/main/subacct/done"
    CONTINUE_SESSION = "/main/continue-session"


class FieldId(StrEnum):
    """Form control names / automationIds in MEMSYS."""

    OPERATOR = "OPR"
    PASSWORD = "PWD"
    MEMBER_ID = "MEMID"
    PRODUCT = "PROD"
    NICKNAME = "NICK"
    AMOUNT = "AMT"
    SOURCE = "SRC"
    SCREEN = "SCRN"
    ACTION = "ACT"


class LabMember(StrEnum):
    """Seed membership numbers in the mock (not secrets)."""

    ACTIVE = "12345"
    SECONDARY = "12346"
    DENIED = "77777"
    LOCKED = "88888"
    MISSING = "99999"


class ProductCode(StrEnum):
    """Open-sub-account product codes on the mock."""

    SECONDARY_SHARES = "02"
    VACATION_CLUB = "03"
    MONEY_MARKET = "04"


class ButtonLabel(StrEnum):
    """Visible control labels the recorder and policy match on."""

    SIGN_ON = "Sign On"
    SEARCH = "Search"
    OPEN_SUB_ACCOUNT = "Open Sub-Account"
    CANCEL = "Cancel"
    APPROVE = "Approve"
    POST_NEW_SHARE = "Post New Share"
    CONTINUE_SESSION = "Continue Session"
    DISMISS = "Dismiss"
    SIGN_OFF = "Sign Off"


class RecoveryAction(StrEnum):
    """Named recovery the replay engine can attempt on an overlay."""

    CONTINUE_SESSION = "continue_session"
    DISMISS = "dismiss"


class FlowId(StrEnum):
    """Default capability ids."""

    LOOKUP_SAVINGS_BALANCE = "lookup_savings_balance"


class SecretToken(StrEnum):
    """Non-secret stand-ins stored in recordings instead of real credentials."""

    PASSWORD = "x_password"
    OPERATOR = "x_operator"


class PageNeedle(StrEnum):
    """Lowercased phrases classify_runtime looks for on the page."""

    NOT_FOUND = "record not found for membership"
    SERVICING_RIGHTS = "does not have servicing rights"
    LOCKED = "is in locked status"
    INSUFFICIENT_FUNDS = "insufficient available balance"
    MEMBER_ID_REQUIRED = "member id is required"
    MEMBER_ID_NUMERIC = "member id must be numeric"
    PRODUCT_REQUIRED = "product type is required"
    DEPOSIT_INVALID = "opening deposit must be"
    HUMAN_APPROVAL = "human approval required"
    VERIFY_HUMAN = "verify you are human"
    SESSION_TIMEOUT = "session timeout"
    CONTINUE_SESSION = "continue session"
    SYSTEM_MESSAGE = "memsys — system message"
    CORE_WINDOW = "core processing window is active"


SCHEMA_VERSION = "capability.v1"
DEFAULT_ORIGIN = "http://127.0.0.1:8000"
DEFAULT_HOSTS = ("127.0.0.1", "localhost")
DEFAULT_TENANTS_PATH = "tenants/tenants.json"
DEFAULT_POLICY_PATH = "policies/default.json"
DEFAULT_WORKFLOWS_DIR = "workflows"
DEFAULT_EVIDENCE_DIR = "evidence"
DEFAULT_RUNS_DIR = "runs"
DEFAULT_VIEWPORT_WIDTH = 1280
DEFAULT_VIEWPORT_HEIGHT = 800
CATALOG_FILE = "catalog.json"

ALLOWED_ACTIONS = (
    ActionKind.NAVIGATE,
    ActionKind.CLICK,
    ActionKind.CHANGE,
    ActionKind.KEY_DOWN,
    ActionKind.WAIT,
    ActionKind.SCROLL,
    ActionKind.EXTRACT,
    ActionKind.SCREENSHOT,
    ActionKind.DONE,
    ActionKind.ASK_HUMAN,
)

ALLOWED_PATHS = (
    UrlPath.ROOT,
    UrlPath.SIGNON,
    UrlPath.MAIN,
    UrlPath.LAB,
    UrlPath.HEALTH,
)

IRREVERSIBLE_LABELS = (ButtonLabel.POST_NEW_SHARE,)
