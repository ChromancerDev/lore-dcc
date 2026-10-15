"""Open a prefilled GitHub bug report for the plugin.

Each DCC's File a Bug entry calls `open_report` with its own name and version.
"""

from __future__ import annotations

import platform
import webbrowser
from urllib.parse import urlencode

from lore_dcc import __version__

NEW_ISSUE = "https://github.com/ChromancerDev/lore-dcc/issues/new"
TEMPLATE = "bug_report.yml"


def report_url(dcc: str, dcc_version: str) -> str:
    """Return the new-issue link with the bug form's text fields filled in.

    No `labels` parameter: GitHub answers 404 to users who cannot add labels.
    """
    query = {
        "template": TEMPLATE,
        "dcc": dcc,
        "dcc-version": dcc_version,
        "plugin-version": __version__,
        "os": platform.platform(),
    }
    return f"{NEW_ISSUE}?{urlencode(query)}"


def open_report(dcc: str, dcc_version: str) -> bool:
    """Open the bug form in the default browser. Returns False if none opened."""
    return webbrowser.open(report_url(dcc, dcc_version))
