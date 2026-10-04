"""Qt dialogs shared by Qt-native DCCs. PySide6 from the host only.

The host calls `set_parent_provider` once at startup so dialogs parent to its
main window. Nothing in this package imports a DCC module.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

_parent_provider: Callable[[], Any] | None = None


def set_parent_provider(provider: Callable[[], Any]) -> None:
    """Register a callable that returns the host's main window, or None."""
    global _parent_provider
    _parent_provider = provider


def parent_window() -> Any:
    """Return the host main window, or None when no provider is set."""
    return _parent_provider() if _parent_provider else None


_prefs_folder: Path | None = None


def set_prefs_folder(path: Path | str | None) -> None:
    """Register the folder the dialogs read `prefs.load_prefs` from, or None for defaults."""
    global _prefs_folder
    _prefs_folder = Path(path) if path else None


def prefs_folder() -> Path | None:
    """Return the registered prefs folder, or None when none is set."""
    return _prefs_folder
