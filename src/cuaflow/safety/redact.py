from __future__ import annotations

import re
from typing import Any

_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD_RUN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    """Return True if digits pass the Luhn check for a card number."""
    total = 0
    alt = False
    for ch in reversed(digits):
        n = ord(ch) - 48
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def _mask_cards(text: str) -> str:
    """Replace likely credit-card numbers in text."""
    def repl(match: re.Match[str]) -> str:
        """Mask one regex match if it looks like a card."""
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            return f"«card:{digits[-4:]}»"
        return match.group(0)

    return _CARD_RUN.sub(repl, text)


class Redactor:
    """Hide secrets and obvious PII before anything is written to disk."""

    _PII_KEYS = {"password", "token", "secret", "ssn", "member_name", "name", "card", "operator"}

    def __init__(self) -> None:
        """Start with no registered secret values."""
        self._secrets: list[tuple[str, str]] = []

    def register(self, value: str, label: str) -> None:
        """Remember a secret so later scrub() can replace it."""
        if value:
            self._secrets.append((value, label))
            self._secrets.sort(key=lambda item: len(item[0]), reverse=True)

    def scrub(self, data: Any) -> Any:
        """Walk a string/dict/list and hide secrets and PII keys."""
        if isinstance(data, str):
            return self._scrub_text(data)
        if isinstance(data, dict):
            out: dict[str, Any] = {}
            for key, value in data.items():
                if self._hidden_key(str(key)):
                    out[key] = "«redacted»" if value not in (None, "") else value
                else:
                    out[key] = self.scrub(value)
            return out
        if isinstance(data, list):
            return [self.scrub(item) for item in data]
        if isinstance(data, tuple):
            return tuple(self.scrub(item) for item in data)
        return data

    def _hidden_key(self, key: str) -> bool:
        """Return True if this dict key should never be stored in the clear."""
        lowered = key.lower()
        if lowered in self._PII_KEYS:
            return True
        return (
            "password" in lowered
            or "token" in lowered
            or lowered.endswith("_secret")
            or lowered.endswith("operator")
        )

    def _scrub_text(self, text: str) -> str:
        """Replace registered secrets, SSNs, and card numbers in a string."""
        for value, label in self._secrets:
            if value:
                text = re.sub(re.escape(value), f"«{label}»", text, flags=re.I)
        text = _SSN.sub("«ssn»", text)
        return _mask_cards(text)
