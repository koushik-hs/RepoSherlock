"""Best-effort secret masking that preserves the original line boundaries."""

import re

MASK = "[REDACTED]"
PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?"
    r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|\Z)"
)
KEY_PREFIXES = re.compile(
    r"(?<![A-Za-z0-9])(?:AIza[A-Za-z0-9_-]{35}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{12,}|sk-[A-Za-z0-9_-]{16,}|"
    r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{12,})(?![A-Za-z0-9])"
)
AUTH_HEADER = re.compile(r"(?i)\b(?:bearer|basic)[ \t]+([A-Za-z0-9_./+=~-]{8,})")
URL_CREDENTIALS = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*://)([^/\s:@]+:[^/\s@]+)@")
ASSIGNMENT = re.compile(
    r"""(?ix)\b(?:[a-z][a-z0-9]*[-_])*(?:api[-_]?key|client[-_]?secret|private[-_]?key|
    password|passwd|authorization|[a-z][a-z0-9]*[-_]?token|token|secret)
    ["']?[ \t]*[:=][ \t]*
    (?P<value> "(?:\\.|[^"\\\r\n])*" | '(?:\\.|[^'\\\r\n])*' | [^ \t\r\n,;#"'{}\[\]()]+ )
    """
)
# A PEM body or encoded credential can appear in a chunk without its header.
ENCODED_VALUE = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{48,}(?![A-Za-z0-9+/=_-])")


def _mask_group(match: re.Match[str]) -> str:
    start, end = match.span(1)
    return match.string[match.start():start] + MASK + match.string[end:match.end()]


def redact(text: str) -> str:
    text = PRIVATE_KEY.sub(lambda match: re.sub(r"[^\r\n]+", MASK, match.group()), text)
    text = URL_CREDENTIALS.sub(_mask_group, text)
    text = AUTH_HEADER.sub(_mask_group, text)
    text = ASSIGNMENT.sub(_mask_group, text)
    return ENCODED_VALUE.sub(MASK, KEY_PREFIXES.sub(MASK, text))
