"""Secret redaction for logs. Anything token-shaped is cut to its first 6 characters."""
from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*"),         # JWTs
    re.compile(r"sk-ant-[\w-]+"),                      # Anthropic OAuth tokens
    re.compile(r"ya29\.[\w.-]+"),                      # Google access tokens
    re.compile(r"1//[\w-]{20,}"),                      # Google refresh tokens
    re.compile(r"rt\.\d\.[\w.-]+"),                    # OpenAI refresh tokens
    re.compile(r"GOCSPX-[\w-]+"),                      # Google OAuth client secrets
]
_KEYED = re.compile(
    r'(?i)("?(?:access_?token|refresh_?token|id_?token|token|key|secret|cookie|authorization|password)"?\s*[:=]\s*"?)'
    r'(?:Bearer\s+)?([^\s",}]{7,})'
)
_BEARER = re.compile(r"(?i)(Bearer\s+)(\S{7,})")
_EMAIL = re.compile(r"[\w.+-]+(?:@|%40)[\w-]+\.[\w.]+")


def _cut(s: str) -> str:
    return s[:6] + "…[redacted]"


def redact(text: str) -> str:
    if not text:
        return text
    for p in _PATTERNS:
        text = p.sub(lambda m: _cut(m.group(0)), text)
    text = _KEYED.sub(lambda m: m.group(1) + (m.group(2) if m.group(2).endswith("[redacted]") else _cut(m.group(2))), text)
    text = _BEARER.sub(lambda m: m.group(1) + (m.group(2) if m.group(2).endswith("[redacted]") else _cut(m.group(2))), text)
    text = _EMAIL.sub("<email>", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True
