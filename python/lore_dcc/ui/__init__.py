"""Qt dialogs shared by Qt-native DCCs. PySide6 from the host only.

The host calls `set_parent_provider` once at startup so dialogs parent to its
main window. Nothing in this package imports a DCC module.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

TITLE_PREFIX = "Lore - "

_parent_provider: Callable[[], Any] | None = None


def window_title(title: str) -> str:
    """Return `title` as a window title that starts with `Lore - `.

    A blank title or a bare `Lore` becomes `Lore - Info`.
    """
    title = title.strip()
    if title.startswith(TITLE_PREFIX):
        return title
    if title in ("", "Lore"):
        return f"{TITLE_PREFIX}Info"
    return f"{TITLE_PREFIX}{title}"


def set_parent_provider(provider: Callable[[], Any]) -> None:
    """Register a callable that returns the host's main window, or None."""
    global _parent_provider
    _parent_provider = provider


def parent_window() -> Any:
    """Return the host main window, or None when no provider is set."""
    return _parent_provider() if _parent_provider else None
