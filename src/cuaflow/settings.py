from __future__ import annotations

import re
from urllib.parse import urlparse

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from cua.enums import (
    DEFAULT_EVIDENCE_DIR,
    DEFAULT_ORIGIN,
    DEFAULT_POLICY_PATH,
    DEFAULT_RUNS_DIR,
    DEFAULT_TENANTS_PATH,
    DEFAULT_VIEWPORT_HEIGHT,
    DEFAULT_VIEWPORT_WIDTH,
    DEFAULT_WORKFLOWS_DIR,
    Placeholder,
    RecordedUrl,
    UrlPath,
)

load_dotenv()

_BASE_TOKEN = re.compile(r"\{base_url\}|\bbase_url\b", re.I)
_BASE_WITH_PATH = re.compile(r"(?:\{base_url\}|\bbase_url\b)(/(?:[^\s]*))", re.I)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_file_encoding="utf-8")

    memsys_base_url: str = DEFAULT_ORIGIN
    memsys_operator: str = "JLEE"
    memsys_password: str = "demo"

    browser_use_api_key: str | None = None
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"

    cua_max_discover_steps: int = 40
    cua_viewport_width: int = DEFAULT_VIEWPORT_WIDTH
    cua_viewport_height: int = DEFAULT_VIEWPORT_HEIGHT
    cua_runs_dir: str = DEFAULT_RUNS_DIR
    cua_workflows_dir: str = DEFAULT_WORKFLOWS_DIR
    cua_policy_path: str = DEFAULT_POLICY_PATH
    cua_tenants_path: str = DEFAULT_TENANTS_PATH
    cua_evidence_dir: str = DEFAULT_EVIDENCE_DIR


def load_settings() -> Settings:
    """Load Settings from environment and .env."""
    return Settings()


def web_origin(settings: Settings | None = None, *, origin: str | None = None) -> str:
    """Host root: tenant origin if given, else MEMSYS_BASE_URL in .env (no trailing slash)."""
    if (origin or "").strip():
        return origin.strip().rstrip("/")
    raw = (settings or load_settings()).memsys_base_url.strip() or DEFAULT_ORIGIN
    return raw.rstrip("/")


def web_start_url(settings: Settings | None = None, *, origin: str | None = None) -> str:
    """Where a web run opens. Origin-only values get /signon."""
    bound = web_origin(settings, origin=origin)
    path = urlparse(bound).path
    if path in {"", "/"}:
        return bound + UrlPath.SIGNON
    return bound


def expand_base_url(text: str, settings: Settings | None = None, *, origin: str | None = None) -> str:
    """Turn 'open base_url' / 'open base_url/main/...' into the bound origin. Desktop never needs this."""
    bound = web_origin(settings, origin=origin)
    start = web_start_url(settings, origin=origin)

    def with_path(match: re.Match[str]) -> str:
        """Replace a base_url/path token with the bound origin plus that path."""
        return bound + match.group(1).rstrip(".,);")

    text = _BASE_WITH_PATH.sub(with_path, text or "")
    return _BASE_TOKEN.sub(start, text)


def fold_recorded_url(url: str, settings: Settings | None = None, *, origin: str | None = None) -> str:
    """Store {base_url}/path when the host matches env or the tenant origin. Other hosts stay literal."""
    raw = (url or "").strip()
    if not raw or raw.startswith("{") or raw.lower().startswith("desktop:"):
        if raw.lower() == "base_url":
            return RecordedUrl.SIGNON
        return raw
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return raw
    hosts = set()
    for candidate in (web_origin(settings), (origin or "").strip().rstrip("/") or None):
        if not candidate:
            continue
        host = (urlparse(candidate).hostname or "").lower()
        if host:
            hosts.add(host)
    if (parsed.hostname or "").lower() not in hosts:
        return raw
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    if path in {"", "/"}:
        return RecordedUrl.SIGNON
    return Placeholder.BASE_URL + path


def to_placeholder_url(token: str, settings: Settings | None = None, *, origin: str | None = None) -> str:
    """Map prompt tokens (base_url, base_url/path, or a recorded http URL) to {base_url}/path."""
    raw = (token or "").strip().rstrip(".,);")
    lower = raw.lower()
    if not raw:
        return RecordedUrl.SIGNON
    if lower.startswith("desktop:"):
        return raw
    if lower in {"app", "memsys", "desktop"}:
        return RecordedUrl.SIGNON
    if lower.startswith(("http://", "https://")):
        return fold_recorded_url(raw, settings, origin=origin)
    if lower in {"base_url", Placeholder.BASE_URL}:
        return RecordedUrl.SIGNON
    if lower.startswith("base_url/"):
        return Placeholder.BASE_URL + raw[len("base_url") :]
    if lower.startswith(Placeholder.BASE_URL):
        rest = raw[len(Placeholder.BASE_URL) :]
        return Placeholder.BASE_URL + (rest if rest else UrlPath.SIGNON)
    return RecordedUrl.SIGNON


def prompt_token_from_url(url: str, settings: Settings | None = None) -> str:
    """Canonical wording: 'base_url' or 'base_url/path' — never a host."""
    folded = to_placeholder_url(url, settings)
    if folded == RecordedUrl.SIGNON:
        return "base_url"
    if folded.startswith(Placeholder.BASE_URL):
        return "base_url" + folded[len(Placeholder.BASE_URL) :]
    return url
