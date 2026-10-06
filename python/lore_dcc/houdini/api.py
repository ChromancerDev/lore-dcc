"""Helpers for a ROP's Pre-Render and Post-Render Python parameters.

Set Pre-Render to `from lore_dcc.houdini import api; api.pre_render()` and
Post-Render to `from lore_dcc.houdini import api; api.post_render()`.

In Houdini's UI a failure shows a dialog. In hython and batch renders it is
raised, so the render stops before it writes over a file someone else locked.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import hou

from lore_dcc.errors import NotAWorkingCopy
from lore_dcc.houdini import host, outputs
from lore_dcc.session import Session, WriteResult

logger = logging.getLogger(__name__)


def pre_render(node: Any = None) -> tuple[str, ...]:
    """Lock the tracked files `node` is about to write.

    Args:
        node: The ROP or File Cache. Defaults to `hou.pwd()`.

    Returns:
        The repo-relative paths this call locked.
    """
    return _guarded(node, (), lambda s, p: s.prepare_write(p), "Locking outputs") or ()


def post_render(node: Any = None) -> WriteResult | None:
    """Report the files `node` wrote: stage new ones, release unchanged ones.

    Args:
        node: The ROP or File Cache. Defaults to `hou.pwd()`.
    """
    return _guarded(node, None, lambda s, p: s.finish_write(p), "Collecting outputs")


def _guarded[T](
    node: Any, empty: T, step: Callable[[Session, list[Path]], T], title: str
) -> T:
    node = node if node is not None else hou.pwd()
    try:
        if not host.load_prefs().enabled:
            return empty
        session = host.session_for(hou.hipFile.path())
        paths = outputs.outputs_for_node(node, session)
        return host.run(step, session, paths, title=title) if paths else empty
    except NotAWorkingCopy:
        return empty
    except Exception as exc:
        if not host.has_ui():
            raise
        logger.exception("Lore render helper failed on %s", node.path())
        host.error(exc, f"Lore on {node.path()}")
        return empty
