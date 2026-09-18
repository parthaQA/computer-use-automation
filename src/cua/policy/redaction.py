from __future__ import annotations

import re

_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[PAN]"),
    (re.compile(r"(?i)password\s*[:=]\s*\S+"), "password=[REDACTED]"),
    (re.compile(r"(?i)(sk-|ak-|key=)\S+"), "[SECRET]"),
]


def redact(text: str) -> str:
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out
