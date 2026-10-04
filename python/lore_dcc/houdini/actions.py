"""Shelf, node menu and parameter menu actions.

Each public function is one menu or shelf entry. Errors reach the user through
`host.error`; nothing here raises to Houdini.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import hou

from lore_dcc.errors import LoreError, NotAWorkingCopy
from lore_dcc.houdini import hooks, host, outputs
from lore_dcc.locks import LockState
from lore_dcc.session import Session, WriteResult, new_file_message

logger = logging.getLogger(__name__)

TITLE = "Lore"
_LISTED = 8


def _reported(func: Callable[..., None]) -> Callable[..., None]:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            func(*args, **kwargs)
        except LoreError as exc:
            host.error(exc, TITLE)
        except Exception as exc:
            logger.exception("Lore action %s failed", func.__name__)
            host.error(exc, TITLE)

    return wrapper


def _session(file: Path | str) -> Session | None:
    """Return the Session for `file`, telling the user why when there is none."""
    if not host.load_prefs().enabled:
        host.message(
            "Lore - Disabled",
            "Lore is turned off. Use Enable/Disable in the Lore menu or on the shelf to turn it on.",
        )
        return None
    try:
        return host.session_for(file)
    except NotAWorkingCopy:
        host.message("Lore - Not a Working Copy", "Not a Lore working copy")
        return None


def _hip() -> str:
    return hou.hipFile.path()


def _saved_hip() -> str | None:
    file = _hip()
    if not os.path.isfile(file):
        host.message("Lore - Save File First", "Save the file first.")
        return None
    return file


def _rel(session: Session, file: Path | str) -> str:
    return session.workspace.relative(Path(file))


def _library_file(node: Any) -> str | None:
    return hooks.library_file(node.type().definition())


def _parm_file(parm: Any, *, must_exist: bool = False) -> str | None:
    """Return the absolute file a string parm points at, or None after telling the user why."""
    value = hou.text.expandString(parm.evalAsString())
    if not value.strip():
        host.message(
            "Lore - Parameter File", f"The {parm.name()} parameter is empty. Set it to a file first."
        )
        return None
    path = os.path.abspath(value)
    if os.path.isdir(path):
        host.message("Lore - Parameter File", f"{path} is a folder. Pick a file.")
        return None
    if must_exist and not os.path.isfile(path):
        host.message("Lore - Parameter File", f"{path} does not exist.")
        return None
    return path


def _with_notes(text: str, notes: list[str]) -> str:
    return "\n".join([text, *(f"Warning: {note}" for note in notes)])


def describe_write(result: WriteResult) -> str:
    """Return a short summary of what finish_write did."""
    lines: list[str] = []
    for label, paths in (
        ("Staged (new)", result.staged),
        ("Changed (lock kept)", result.changed),
        ("Released (unchanged)", result.released),
    ):
        if paths:
            lines.append(f"{label}: {len(paths)}")
            lines += [f"    {p}" for p in paths[:_LISTED]]
            if len(paths) > _LISTED:
                lines.append(f"    and {len(paths) - _LISTED} more")
    return "\n".join(lines) or "No output files changed."


def node_has_library(node: Any) -> bool:
    """Return True when the node is an instance of an asset saved in a library file."""
    return _library_file(node) is not None


def node_is_output(node: Any) -> bool:
    """Return True when the node's type is in the output table."""
    return outputs.is_output_node(node)


def node_menu_visible(node: Any) -> bool:
    """Return True when the Lore node menu has an entry for this node."""
    return node_has_library(node) or node_is_output(node)


@_reported
def info() -> None:
    """Show working copy, branch, identity and remote for the open file."""
    from lore_dcc.ui import dialogs

    session = _session(_hip())
    if session:
        dialogs.show_info(session)


@_reported
def toggle_enabled() -> None:
    """Turn the Lore hooks on or off for this user."""
    prefs = host.load_prefs()
    prefs = dataclasses.replace(prefs, enabled=not prefs.enabled)
    host.save_prefs(prefs)
    if prefs.enabled:
        host.message("Lore - Enabled", "Lore is now enabled.")
    else:
        host.message("Lore - Disabled", "Lore is now disabled.")


@_reported
def lock() -> None:
    """Lock the open file."""
    file = _saved_hip()
    session = _session(file) if file else None
    if file and session:
        _lock_files(session, [file])


@_reported
def discard() -> None:
    """Discard changes to the open file, then reload it."""
    file = _saved_hip()
    session = _session(file) if file else None
    if not (file and session):
        return
    if _discard_files(session, [file], "Unsaved changes in this session are lost."):
        hou.hipFile.load(file, suppress_save_prompt=True)


@_reported
def collect_outputs() -> None:
    """Scan the whole scene for files it writes and report them to Lore."""
    session = _session(_hip())
    if session:
        notes: list[str] = []
        _collect(session, outputs.scan_scene(session, notes), notes)


@_reported
def commit() -> None:
    """Open the Commit dialog."""
    from lore_dcc.ui import dialogs

    session = _session(_hip())
    if session:
        dialogs.show_commit(session)


@_reported
def lock_hda(node: Any) -> None:
    """Lock the library file that defines the node's asset."""
    file = _library_file(node)
    session = _session(file) if file else None
    if file and session:
        _lock_files(session, [file])


@_reported
def discard_hda(node: Any) -> None:
    """Discard changes to the node's library file, then reload it."""
    file = _library_file(node)
    session = _session(file) if file else None
    if not (file and session):
        return
    if _discard_files(session, [file], "Unsaved edits to the asset are lost."):
        hou.hda.reloadFile(file)


@_reported
def lock_node_outputs(node: Any) -> None:
    """Lock the tracked files this node is about to write."""
    session = _session(_hip())
    if not session:
        return
    notes: list[str] = []
    paths = outputs.outputs_for_node(node, session, notes)
    locked = host.run(session.prepare_write, paths, title="Locking outputs") if paths else ()
    if locked:
        text = f"Locked {len(locked)} output file(s)."
    elif paths and _all_mine(session, paths):
        text = "Already locked by you."
    else:
        text = "Nothing to lock. New files need no lock."
    host.message("Lore - Lock Outputs", _with_notes(text, notes))


@_reported
def collect_node_outputs(node: Any) -> None:
    """Report the files this node wrote to Lore."""
    session = _session(_hip())
    if session:
        notes: list[str] = []
        _collect(session, outputs.outputs_for_node(node, session, notes), notes)


@_reported
def lock_parm_file(parm: Any) -> None:
    """Lock the file a string parameter points at."""
    file = _parm_file(parm)
    session = _session(file) if file else None
    if file and session:
        _lock_files(session, [file])


@_reported
def discard_parm_file(parm: Any) -> None:
    """Discard changes to the file a string parameter points at."""
    file = _parm_file(parm, must_exist=True)
    session = _session(file) if file else None
    if file and session:
        _discard_files(session, [file], "")


def _all_mine(session: Session, paths: list[Path]) -> bool:
    states = host.run(session.file_states, paths)
    held = [s for s in states if s.tracked and s.lockable]
    return bool(held) and all(s.lock is LockState.LOCKED_BY_ME for s in held)


def _collect(session: Session, paths: list[Path], notes: list[str]) -> None:
    if not paths:
        host.message(
            "Lore - Collect Outputs",
            _with_notes("This scene writes no files inside the working copy.", notes),
        )
        return
    result = host.run(session.finish_write, paths, title="Collecting outputs")
    host.message("Lore - Collect Outputs", _with_notes(describe_write(result), notes))


def _lock_files(session: Session, files: list[str]) -> None:
    host.run(session.lock, files, title="Locking")
    names = ", ".join(_rel(session, f) for f in files)
    host.message("Lore - Lock File", f"Locked {names}.")


def _discard_files(session: Session, files: list[str], warning: str) -> bool:
    states = host.run(session.file_states, files)
    new = [s.path for s in states if not s.tracked]
    if new:
        host.message("Lore - Discard Changes", "\n".join(new_file_message(path) for path in new))
        return False
    names = "\n".join(f"    {_rel(session, f)}" for f in files)
    parts = [f"Discard changes to:\n{names}", warning, "This cannot be undone."]
    text = "\n\n".join(part for part in parts if part)
    choice = host.ask(
        "Lore - Discard Changes?", text, ["Discard changes", "Cancel"], default="Cancel"
    )
    if choice != "Discard changes":
        return False
    host.run(session.discard, files, title="Discarding changes")
    return True
