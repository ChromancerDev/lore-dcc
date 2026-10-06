"""Lore calls for scripts: ROP Pre-Render and Post-Render parameters, HDA buttons, Python SOPs.

Set a ROP's Pre-Render to `from lore_dcc.houdini import api; api.pre_render()`
and Post-Render to `from lore_dcc.houdini import api; api.post_render()`.
A script that writes files itself calls `api.lock(paths)` before writing and
`api.stage(paths)` after.

In Houdini's UI a failure shows a dialog. In hython and batch renders it is
raised, so the job stops before it writes over a file someone else locked.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import hou

from lore_dcc.errors import NotAWorkingCopy
from lore_dcc.houdini import host, outputs
from lore_dcc.session import Session, WriteResult

logger = logging.getLogger(__name__)

Paths = str | Path | Iterable[str | Path]


def pre_render(node: Any = None) -> tuple[str, ...]:
    """Lock the tracked files `node` is about to write.

    Args:
        node: The ROP or File Cache. Defaults to `hou.pwd()`.

    Returns:
        The repo-relative paths this call locked.
    """
    node = node if node is not None else hou.pwd()
    return _node(node, (), lambda s, p: s.prepare_write(p), "Locking outputs")


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
            `$HIP/geo/out.bgeo.sc`. Paths outside the working copy are ignored.

    Returns:
        The repo-relative paths this call locked.
    """
    return _files(paths, (), lambda s, p: s.prepare_write(p), "Locking files")


def stage(paths: Paths) -> WriteResult | None:
    """Report files a script wrote: stage new ones, release the locks on unchanged ones.

    Changed tracked files keep their lock and wait for Commit, like
    `post_render`.

    Args:
        paths: One path or several, absolute or with variables such as
            `$HIP/tex/albedo.exr`. Paths outside the working copy are ignored.
    """
    return _files(paths, None, lambda s, p: s.finish_write(p), "Collecting files")


def _node[T](node: Any, empty: T, step: Callable[[Session, list[Path]], T], title: str) -> T:
    return _run(
        hou.hipFile.path(), lambda s: outputs.outputs_for_node(node, s), empty, step, title, node.path()
    )


def _files[T](paths: Paths, empty: T, step: Callable[[Session, list[Path]], T], title: str) -> T:
    values = [paths] if isinstance(paths, str | Path) else list(paths)
    files = [hou.text.expandString(str(value)) for value in values]
    if not files:
        return empty
    return _run(
        files[0],
        lambda s: outputs.paths_in_working_copy(files, s.workspace.root),
        empty,
        step,
        title,
        files[0],
    )


def _run[T](
    anchor: str,
    find: Callable[[Session], list[Path]],
    empty: T,
    step: Callable[[Session, list[Path]], T],
    title: str,
    where: str,
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
        return empty
