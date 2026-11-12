"""Glue between Designer and the plugin core: prefs, UI gating, threaded Lore calls.

Every dialog helper falls back to logging when Designer has no UI, so
command-line runs never open a window.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import sd

from lore_dcc import prefs as prefs_module
from lore_dcc import session as session_module
from lore_dcc.prefs import Prefs
from lore_dcc.session import Session

logger = logging.getLogger(__name__)


def app() -> Any:
    """Return Designer's SDApplication."""
    return sd.getContext().getSDApplication()


def qt_ui() -> Any:
    """Return the Qt UI manager, or None without a UI."""
    return app().getQtForPythonUIMgr()


def has_ui() -> bool:
    """Return True in a graphical Designer session."""
    return qt_ui() is not None


def prefs_folder() -> Path:
    """Return the folder that holds the per-user prefs file.

    Two levels above the user plugins folder, so deleting the plugin to
    update it keeps the prefs.
    """
    return Path(app().getPluginMgr().getUserPluginsDir()).parent.parent


def setup() -> None:
    """Point the shared UI at Designer's main window."""
    from lore_dcc import ui

    if has_ui():
        ui.set_parent_provider(lambda: qt_ui().getMainWindow())


def load_prefs() -> Prefs:
    """Read the user's prefs."""
    return prefs_module.load_prefs(prefs_folder())


def save_prefs(prefs: Prefs) -> None:
    """Write the user's prefs."""
    prefs_module.save_prefs(prefs_folder(), prefs)


def run[T](fn: Callable[..., T], *args: object, title: str = "Working") -> T:
    """Run a Lore call off the main thread in the UI, inline without one.

    `fn` must not touch `sd`.
    """
    if not has_ui():
        return fn(*args)
    from lore_dcc.ui import tasks

    return tasks.run_blocking(fn, *args, title=title)


def later(fn: Callable[[], None]) -> None:
    """Run `fn` on Designer's main thread once the current callback has returned.

    Inline without a UI.
    """
    if not has_ui():
        fn()
        return
    from PySide6 import QtCore

    QtCore.QTimer.singleShot(0, fn)


def session_for(path: Path | str) -> Session:
    """Return the Session for the working copy holding `path`.

    Raises:
        NotAWorkingCopy: `path` is not in a Lore working copy.
        LoreError: Lore is missing or too old.
    """
    return run(session_module.session_for, path, session_module.DESIGNER)


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
    from lore_dcc import bugs

    bugs.remember_error(exc, context)
    if not has_ui():
        logger.error("%s: %s", context or "Lore", exc)
        return
    from lore_dcc.ui import messages

    messages.show_error(exc, context)
