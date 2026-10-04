"""Glue between Houdini and the plugin core: prefs, UI gating, threaded Lore calls.

Every dialog helper falls back to logging when Houdini has no UI, so hython
and batch renders never open a window.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path

import hou

from lore_dcc import prefs as prefs_module
from lore_dcc import session as session_module
from lore_dcc.houdini import outputs
from lore_dcc.prefs import Prefs
from lore_dcc.session import Session

logger = logging.getLogger(__name__)


def has_ui() -> bool:
    """Return True in a graphical Houdini session."""
    return bool(hou.isUIAvailable())


def prefs_folder() -> Path:
    """Return the folder that holds the per-user prefs file."""
    return Path(hou.homeHoudiniDirectory())


def setup() -> None:
    """Point the shared UI at Houdini's main window."""
    from lore_dcc import ui

    if has_ui():
        ui.set_parent_provider(lambda: hou.qt.mainWindow())


def load_prefs() -> Prefs:
    """Read the user's prefs."""
    return prefs_module.load_prefs(prefs_folder())


def save_prefs(prefs: Prefs) -> None:
    """Write the user's prefs."""
    prefs_module.save_prefs(prefs_folder(), prefs)


def run[T](fn: Callable[..., T], *args: object, title: str = "Working") -> T:
    """Run a Lore call off the main thread in the UI, inline without one.

    `fn` must not touch `hou`.
    """
    if not has_ui():
        return fn(*args)
    from lore_dcc.ui import tasks

    return tasks.run_blocking(fn, *args, title=title)


def session_for(path: Path | str) -> Session:
    """Return the Session for the working copy holding `path`.

    Raises:
        NotAWorkingCopy: `path` is not in a Lore working copy.
        LoreError: Lore is missing or too old.
    """
    session = run(session_module.session_for, path)
    outputs.remember_overrides(session.settings.houdini)
    return session


def ask(
    title: str, text: str, buttons: Sequence[str], default: str | None = None
) -> str | None:
    """Ask a question. Without a UI, log it and return None."""
    if not has_ui():
        logger.info("%s: %s (no UI, nothing chosen)", title, text)
        return None
    from lore_dcc.ui import messages

    return messages.ask(title, text, buttons, default)


def message(title: str, text: str) -> None:
    """Show a message, or log it without a UI."""
    if not has_ui():
        logger.info("%s: %s", title, text)
        return
    from lore_dcc.ui import messages

    messages.show_message(title, text)


def error(exc: BaseException, context: str = "") -> None:
    """Show an error, or log it without a UI."""
    if not has_ui():
        logger.error("%s: %s", context or "Lore", exc)
        return
    from lore_dcc.ui import messages

    messages.show_error(exc, context)
