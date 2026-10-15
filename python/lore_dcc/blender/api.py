"""Lore calls for scripts: exporters, bake and render scripts, the render handlers.

A script that writes files itself calls `api.lock(paths)` before writing and
`api.stage(paths)` after. `pre_render(scene)` and `post_render(scene)` do the
same for a render's frames and File Output images; the add-on runs them from
the render handlers when the scene's "Lock and collect render outputs" box
is on.

These calls wait for Lore on the calling thread, since a script must not
write before its lock is held. `lock` and `stage` raise on failure, after a
popup in the UI, so the calling script stops too.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import bpy
from lore_dcc.blender import host, outputs
from lore_dcc.blender.flow import Error
from lore_dcc.errors import NotAWorkingCopy
from lore_dcc.session import Session, WriteResult
from lore_dcc.workspace import find_workspace

logger = logging.getLogger(__name__)

Paths = str | os.PathLike | Iterable[str | os.PathLike]


def lock(paths: Paths) -> tuple[str, ...]:
    """Lock the tracked files a script is about to write; new files need nothing.

    Args:
        paths: One path or several, absolute or relative to the .blend with
            `//`. Paths outside every Lore working copy are ignored.

    Returns:
        The repo-relative paths this call locked.

    Raises:
        LoreError: A lock was refused or Lore failed. In the UI a popup shows
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


def pre_render(scene: Any = None) -> tuple[str, ...]:
    """Lock the tracked files the scene's render is about to write.

    Args:
        scene: The scene to render. Defaults to the current scene.

    Returns:
        The repo-relative paths this call locked.
    """
    scene = scene if scene is not None else bpy.context.scene
    return _render(scene, (), lambda s, p: s.prepare_write(p), "Locking render outputs")


def post_render(scene: Any = None) -> WriteResult | None:
    """Report the files the scene's render wrote: stage new ones, release unchanged ones.

    Args:
        scene: The rendered scene. Defaults to the current scene.
    """
    scene = scene if scene is not None else bpy.context.scene
    return _render(scene, None, lambda s, p: s.finish_write(p), "Collecting render outputs")


def _absolute(value: str | os.PathLike) -> str:
    return os.path.normpath(bpy.path.abspath(os.fspath(value)))


def _files[T](paths: Paths, step: Callable[[Session, list[Path]], T], title: str) -> list[T]:
    try:
        if not host.load_prefs().enabled:
            return []
        values = [paths] if isinstance(paths, str | os.PathLike) else list(paths)
        groups: dict[Path, list[str]] = {}
        for value in values:
            file = _absolute(value)
            try:
                groups.setdefault(find_workspace(Path(file)).root, []).append(file)
            except NotAWorkingCopy:
                continue
        results = []
        for files in groups.values():
            session = host.session_for(files[0])
            found = outputs.inside(files, session.workspace.root)
            results.append(step(session, found))
        return results
    except Exception as exc:
        if host.has_ui():
            logger.exception("Lore %s failed", title.lower())
            host.notify(Error(exc, title))
        raise


def _render[T](scene: Any, empty: T, step: Callable[[Session, list[Path]], T], title: str) -> T:
    try:
        if not host.load_prefs().enabled or not bpy.data.filepath:
            return empty
        session = host.session_for(bpy.data.filepath)
        paths = outputs.render_outputs(scene, session.workspace.root)
        return step(session, paths) if paths else empty
    except NotAWorkingCopy:
        return empty
    except Exception as exc:
        if not host.has_ui():
            raise
        logger.exception("Lore render helper failed on scene %s", scene.name)
        host.notify(Error(exc, f"Lore on scene {scene.name}"))
        return empty
