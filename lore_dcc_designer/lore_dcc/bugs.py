"""Open a prefilled GitHub bug report for the plugin.

Each DCC's File a Bug entry calls `open_report` with its own name and version.
Each DCC's error display calls `remember_error`, so the report carries the last
error of the session with server addresses, emails, IDs and user names masked.
"""

from __future__ import annotations

import platform
import re
import webbrowser
from urllib.parse import urlencode

from lore_dcc import __version__

NEW_ISSUE = "https://github.com/ChromancerDev/lore-dcc/issues/new"
TEMPLATE = "bug_report.yml"
MAX_URL = 8000
TRIMMED = "\n[... cut to fit the link; paste the rest if it matters]"

_REDACTIONS = (
    (re.compile(r"\b([a-z][a-z0-9+.-]*)://[^\s'\"<>]+", re.IGNORECASE), r"\1://<server>"),
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "<email>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE),
     "<id>"),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE), "<id>"),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b"), "<ip>"),
    (re.compile(r"([\\/](?:Users|home)[\\/])[^\\/\s'\"]+", re.IGNORECASE), r"\1<user>"),
)

_last_error: str | None = None


def redact(text: str) -> str:
    """Mask server addresses, emails, IDs, IP addresses and home folder names."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def remember_error(error: BaseException, context: str = "") -> None:
    """Keep the text of the error the user was just shown, for the next report."""
    global _last_error
    message = str(error) or type(error).__name__
    lines = getattr(error, "lines", None)
    details = "\n".join(lines) if lines else getattr(error, "output", "") or getattr(error, "raw", "")
    parts = [context, message, (details or "").strip()]
    _last_error = "\n\n".join(p for p in parts if p)


def last_error() -> str | None:
    """Return the redacted last error, or None when there was none this session."""
    return redact(_last_error) if _last_error else None


def report_url(dcc: str, dcc_version: str) -> str:
    """Return the new-issue link with the bug form's text fields filled in.

    The last error goes in the log field, cut so the link stays under MAX_URL.
    No `labels` parameter: GitHub answers 404 to users who cannot add labels.
    """
    query = {
        "template": TEMPLATE,
        "dcc": dcc,
        "dcc-version": dcc_version,
        "plugin-version": __version__,
        "os": platform.platform(),
    }
    url = f"{NEW_ISSUE}?{urlencode(query)}"
    log = last_error()
    if not log:
        return url
    text = log
    while text:
        candidate = f"{url}&{urlencode({'output': text})}"
        if len(candidate) <= MAX_URL:
            return candidate
        text = log[: len(text) * 3 // 4].rstrip() + TRIMMED
        if len(text) <= len(TRIMMED) + 1:
            break
    return url


def open_report(dcc: str, dcc_version: str) -> bool:
    """Open the bug form in the default browser. Returns False if none opened."""
    return webbrowser.open(report_url(dcc, dcc_version))
