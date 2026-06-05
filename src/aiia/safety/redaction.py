"""PIIマスク（redaction）。下書き本文・ダイジェスト掲載テキストに適用。"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_SECRET_KV = re.compile(r"(?i)\b(?:api[_-]?key|secret|password|passwd|token)\b\s*[:=]\s*\S+")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")


@dataclass
class Finding:
    kind: str
    text: str


def _luhn_ok(s: str) -> bool:
    ds = [int(c) for c in s if c.isdigit()]
    if len(ds) < 13:
        return False
    total = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def find_secrets(text: str) -> list[Finding]:
    out: list[Finding] = []
    for m in _CARD.finditer(text):
        if _luhn_ok(m.group()):
            out.append(Finding("card", m.group()))
    for kind, pat in (("ssn", _SSN), ("aws_key", _AWS_KEY), ("secret", _SECRET_KV), ("bearer", _BEARER)):
        for m in pat.finditer(text):
            out.append(Finding(kind, m.group()))
    return out


def redact(text: str) -> str:
    out = _CARD.sub(lambda m: "[REDACTED:card]" if _luhn_ok(m.group()) else m.group(), text)
    out = _SSN.sub("[REDACTED:ssn]", out)
    out = _AWS_KEY.sub("[REDACTED:aws_key]", out)
    out = _SECRET_KV.sub("[REDACTED:secret]", out)
    out = _BEARER.sub("[REDACTED:bearer]", out)
    return out
