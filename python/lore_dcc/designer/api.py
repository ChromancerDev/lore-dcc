"""Lore calls for scripts that export from Designer, such as an .sbsar publish or bitmap export.

A script calls `api.lock(paths)` before writing and `api.stage(paths)` after.
A refused lock always raises, after a dialog in Designer's UI, so the script
stops before it writes over a file someone else locked. Both calls block the
calling thread until Lore answers; on Designer's main thread the UI stays
responsive but takes no input.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path

from lore_dcc.designer import host
from lore_dcc.errors import NotAWorkingCopy
from lore_dcc.session import Session, WriteResult
from lore_dcc.workspace import find_workspace

logger = logging.getLogger(__name__)

Paths = str | os.PathLike | Iterable[str | os.PathLike]


def lock(paths: Paths) -> tuple[str, ...]:
    """Lock the tracked files a script is about to write; new files need nothing.

    Args:
        paths: One path or several. A relative path resolves against
            Designer's working folder, so pass absolute paths. Paths outside
            every Lore working copy are ignored.

    Returns:
        The repo-relative paths this call locked.

    Raises:
        LoreError: A lock was refused or Lore failed. In the UI a dialog shows
            first. Either way the error stops the script before it writes.
    """
    results = _files(paths, lambda s, p: s.prepare_write(p), "Locking files")
    return tuple(p for result in results for p in result)


def stage(paths: Paths) -> WriteResult | None:
    """Report files a script wrote: stage new ones, release the locks on unchanged ones.

    Changed tracked files keep their lock and wait for Commit. Returns None
    when no path is in a working copy.

    Args:
        paths: As for `lock`.

    Raises:
        LoreError: As for `lock`; for example a new file someone else locked.
    """
    results = _files(paths, lambda s, p: s.finish_write(p), "Collecting files")
    if not results:
        return None
    return WriteResult(
        *(tuple(p for r in results for p in getattr(r, f)) for f in ("staged", "changed", "released"))
    )


def _files[T](paths: Paths, step: Callable[[Session, list[Path]], T], title: str) -> list[T]:
    try:
        if not host.load_prefs().enabled:
            return []
        values = [paths] if isinstance(paths, str | os.PathLike) else list(paths)
        groups: dict[Path, list[Path]] = {}
        for value in values:
            file = Path(os.path.abspath(os.fspath(value)))
            try:
                groups.setdefault(find_workspace(file).root, []).append(file)
            except NotAWorkingCopy:
                continue
        results = []
        for files in groups.values():
            session = host.session_for(files[0])
            results.append(host.run(step, session, files, title=title))
        return results
    except Exception as exc:
        if host.has_ui():
            logger.exception("Lore %s failed", title.lower())
            host.error(exc, title)
        raise
