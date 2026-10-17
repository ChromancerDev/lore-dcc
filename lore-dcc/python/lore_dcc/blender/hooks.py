"""Save, load and render handlers.

A Blender handler cannot wait for an answer, and asking before a save would
block the UI on a Lore call. So the lock prompt comes right after the save:
the file is on disk, and nothing reaches the server until it is committed.
Handlers never raise into Blender.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import bpy
from bpy.app.handlers import persistent

from lore_dcc.blender import actions, host
from lore_dcc.blender.flow import Ask, Error, Message, Run
from lore_dcc.errors import LoreError, NotAWorkingCopy
from lore_dcc.locks import LockState
from lore_dcc.session import FileState, Session
from lore_dcc.summaries import behind_offer_text

logger = logging.getLogger(__name__)

_unknown_warned = False
_keep_unlocked: set[str] = set()
_other_warned: set[str] = set()


@persistent
def on_save_post(filepath: str = "", *_args: Any) -> None:
    """`save_post` handler: start the after-save flow for the saved file."""
    try:
        file = filepath or bpy.data.filepath
        if file and host.load_prefs().enabled:
            host.start(after_save(file))
    except Exception:
        logger.exception("Lore save hook failed")


@persistent
def on_load_post(filepath: str = "", *_args: Any) -> None:
    """`load_post` handler: check the opened file's references against the server."""
    try:
        file = filepath or bpy.data.filepath
        if file and host.load_prefs().enabled:
            host.start(after_load(file))
    except Exception:
        logger.exception("Lore load hook failed")


@persistent
def on_render_init(scene: Any, *_args: Any) -> None:
    """`render_init` handler: lock the render's tracked outputs when the scene asks for it."""
    if _render_hooks_on(scene):
        from lore_dcc.blender import api

        api.pre_render(scene)


@persistent
def on_render_complete(scene: Any, *_args: Any) -> None:
    """`render_complete` handler: report the render's outputs when the scene asks for it."""
    if _render_hooks_on(scene):
        from lore_dcc.blender import api

        api.post_render(scene)


def _render_hooks_on(scene: Any) -> bool:
    settings = getattr(scene, "lore", None)
    return bool(settings is not None and settings.render_outputs)


HANDLERS = (
    ("save_post", on_save_post),
    ("load_post", on_load_post),
    ("render_init", on_render_init),
    ("render_complete", on_render_complete),
)


def install() -> None:
    """Register every handler once. Safe to call repeatedly."""
    for name, handler in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler not in handlers:
            handlers.append(handler)


def remove() -> None:
    """Unregister every handler."""
    for name, handler in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        while handler in handlers:
            handlers.remove(handler)


def after_save(file: str):
    """Record a save with Lore, then warn about or offer a lock, then offer to stage a new file."""
    session = yield from _session(file)
    if session is None:
        return
    prefs = host.load_prefs()
    # Blender runs save_post even after some failed writes; marking an unchanged file is harmless.
    try:
        yield Run(session.mark_saved, [file], title="Recording the save")
    except LoreError as exc:
        logger.warning("Could not record the save of %s: %s", file, exc)
    state = yield from _state(session, file)
    if state is None:
        return
    if state.lockable:
        if state.lock is LockState.UNKNOWN:
            yield from _warn_unknown(file)
        elif state.lock is LockState.LOCKED_BY_OTHER:
            yield from _warn_locked_by_other(state, file)
        elif (
            state.lock is LockState.NOT_LOCKED
            and state.tracked
            and prefs.prompt_lock_before_save
            and _file_key(file) not in _keep_unlocked
        ):
            yield from _offer_lock(session, file)
            return
    if not prefs.prompt_stage_after_save:
        return
    if not state.exists or state.tracked or state.blocked or state.lock is LockState.LOCKED_BY_ME:
        return
    if actions.BACKUP.search(file):
        return
    try:
        staged = yield Run(session.is_staged, file)
    except LoreError as exc:
        logger.warning("Could not read the staged files for %s: %s", file, exc)
        return
    if staged:
        return
    choice = yield Ask(
        "Lore - Stage New File?",
        f"{Path(file).name} is not staged in Lore. Stage it now?",
        ["Stage", "Not now"],
        "Stage",
    )
    if choice != "Stage":
        return
    yield from _remember_references(session, file, warn=True)
    try:
        yield Run(session.stage, [file], title="Staging")
    except LoreError as exc:
        yield Error(exc, f"Staging {Path(file).name}")


def after_load(file: str):
    """Forget the cached identity, remember the references, then check them against the server.

    Asks only when something is behind. Each step first checks that `file` is still the open file, and ends the
    flow when another file was opened meanwhile, so the new scene's references
    are never stored under the old file.
    """
    session = yield from _session(file)
    if session is None or not is_open(file):
        return
    yield Run(session.invalidate, title="Reading the working copy")
    if not (yield from _remember_references(session, file, warn=False)):
        return
    try:
        report = yield Run(session.behind, file, title="Checking the server")
    except LoreError as exc:
        logger.warning("Could not check %s against the server: %s", file, exc)
        return
    if not is_open(file):
        return
    if not (report.behind or report.source_behind):
        return
    choice = yield Ask(
        "Lore - Dependencies Behind Server",
        behind_offer_text(report),
        ["Sync Dependencies", "Not now"],
        "Sync Dependencies",
    )
    if choice == "Sync Dependencies":
        yield from actions.sync_dependencies(confirm=False)


def _session(file: str):
    try:
        return (yield Run(host.session_for, file, title="Reading the working copy"))
    except NotAWorkingCopy:
        return None
    except LoreError as exc:
        logger.warning("Lore is unavailable for %s: %s", file, exc)
        return None


def _state(session: Session, file: str):
    try:
        states = yield Run(session.file_states, [file], title="Reading Lore state")
    except LoreError as exc:
        logger.warning("Could not read Lore state of %s: %s", file, exc)
        return None
    return states[0]


def _remember_references(session: Session, file: str, *, warn: bool):
    """Scan the open scene and store its references under `file`.

    Returns False, storing nothing, when `file` is no longer the open file.
    """
    if not is_open(file):
        return False
    try:
        from lore_dcc.blender import references

        scene = references.scan(session.workspace.root)
    except Exception:
        logger.exception("Lore could not read the references of %s", file)
        return True
    yield Run(session.set_references, file, scene.inside, title="Reading references")
    if warn:
        yield from actions.warn_outside(scene)
    return True


def is_open(file: str) -> bool:
    """Return True when `file` is the .blend open now."""
    return bool(file) and _file_key(file) == _file_key(bpy.data.filepath)


def _file_key(file: str) -> str:
    return os.path.normcase(os.path.abspath(file)) if file else ""


def _warn_unknown(file: str):
    global _unknown_warned
    logger.warning("Lock state of %s is unknown; it cannot be staged or committed", file)
    if _unknown_warned or not host.has_ui():
        return
    _unknown_warned = True
    yield Message(
        "Lore - Lock State Unknown",
        "Lore could not tell who holds the lock on this file. The save went ahead, but the "
        "plugin will not stage or commit it until the lock state is known. "
        "Run `lore login` or check the connection.",
    )


def _warn_locked_by_other(state: FileState, file: str):
    key = _file_key(file)
    logger.warning("%s is locked by %s", file, state.owner)
    if key in _other_warned or not host.has_ui():
        return
    _other_warned.add(key)
    yield Message(
        "Lore - File Locked by Another User",
        f"{Path(file).name} is locked in Lore by {state.owner}. The save went ahead, but the "
        "file cannot be staged or committed until they release the lock.",
    )


def _offer_lock(session: Session, file: str):
    name = Path(file).name
    choice = yield Ask(
        "Lore - Lock File?",
        f"{name} is not locked in Lore. Lock it now, so nobody else changes it while you work?",
        ["Lock", "Keep unlocked"],
        "Lock",
    )
    if choice == "Lock":
        try:
            yield Run(session.lock, [file], title=f"Locking {name}")
        except LoreError as exc:
            yield Error(exc, f"Locking {name}")
    elif choice == "Keep unlocked":
        _keep_unlocked.add(_file_key(file))
