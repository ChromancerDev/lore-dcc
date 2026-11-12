"""Lore calls for scripts: ROP Pre-Render and Post-Render parameters, HDA buttons, Python SOPs.

Set a ROP's Pre-Render to `from lore_dcc.houdini import api; api.pre_render()`
and Post-Render to `from lore_dcc.houdini import api; api.post_render()`.
A script that writes files itself calls `api.lock(paths)` before writing and
`api.stage(paths)` after.

A refused lock in `pre_render`, `lock` or `stage` always raises, after a
dialog in Houdini's UI, so the render or the calling script stops before it
writes over a file someone else locked. `post_render` shows its dialog in the
UI and raises only in hython and batch renders.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import hou

from lore_dcc.errors import NotAWorkingCopy
from lore_dcc.houdini import host, outputs
from lore_dcc.session import Session, WriteResult
from lore_dcc.workspace import find_workspace

logger = logging.getLogger(__name__)

Paths = str | os.PathLike | Iterable[str | os.PathLike]


def pre_render(node: Any = None) -> tuple[str, ...]:
    """Lock the tracked files `node` is about to write.

    Args:
        node: The ROP or File Cache. Defaults to `hou.pwd()`.

    Returns:
        The repo-relative paths this call locked.
    """
    node = node if node is not None else hou.pwd()
    return _node(node, (), lambda s, p: s.prepare_write(p), "Locking outputs", raise_in_ui=True)


def post_render(node: Any = None) -> WriteResult | None:
    """Report the files `node` wrote: stage new ones, release unchanged ones.

    Args:
        node: The ROP or File Cache. Defaults to `hou.pwd()`.
    """
    node = node if node is not None else hou.pwd()
    return _node(node, None, lambda s, p: s.finish_write(p), "Collecting outputs")


def lock(paths: Paths) -> tuple[str, ...]:
    """Lock the tracked files a script is about to write; new files need nothing.

    Args:
        paths: One path or several, absolute or with variables such as
            `$HIP/geo/out.bgeo.sc`. A relative path resolves against Houdini's
            working folder, not the scene. Paths outside every Lore working
            copy are ignored.

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

    Changed tracked files keep their lock and wait for Commit, like
    `post_render`. Returns None when no path is in a working copy.

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


def _node[T](
    node: Any,
    empty: T,
    step: Callable[[Session, list[Path]], T],
    title: str,
    *,
    raise_in_ui: bool = False,
) -> T:
    return _run(
        hou.hipFile.path(),
        lambda s: outputs.outputs_for_node(node, s),
        empty,
        step,
        title,
        node.path(),
        raise_in_ui=raise_in_ui,
    )


def _files[T](paths: Paths, step: Callable[[Session, list[Path]], T], title: str) -> list[T]:
    try:
        if not host.load_prefs().enabled:
            return []
        values = [paths] if isinstance(paths, str | os.PathLike) else list(paths)
        groups: dict[Path, list[str]] = {}
        for value in values:
            file = hou.text.expandString(os.fspath(value))
            try:
                groups.setdefault(find_workspace(Path(file)).root, []).append(file)
            except NotAWorkingCopy:
                continue
        results = []
        for files in groups.values():
            session = host.session_for(files[0])
            found = outputs.paths_in_working_copy(files, session.workspace.root)
            results.append(host.run(step, session, found, title=title))
        return results
    except Exception as exc:
        if host.has_ui():
            logger.exception("Lore %s failed", title.lower())
            host.error(exc, title)
        raise


def _run[T](
    anchor: str,
    find: Callable[[Session], list[Path]],
    empty: T,
    step: Callable[[Session, list[Path]], T],
    title: str,
    where: str,
    *,
    raise_in_ui: bool = False,
) -> T:
    try:
        if not host.load_prefs().enabled:
            return empty
        session = host.session_for(anchor)
        paths = find(session)
        return host.run(step, session, paths, title=title) if paths else empty
    except NotAWorkingCopy:
        return empty
    except Exception as exc:
        if not host.has_ui():
            raise
        logger.exception("Lore helper failed on %s", where)
        host.error(exc, f"Lore on {where}")
        if raise_in_ui:
            raise
        return empty
