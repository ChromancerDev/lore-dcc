"""Save, load and asset-save hooks.

A hook never raises into Houdini. Houdini ignores an exception raised in a
Before callback and saves anyway, so these hooks warn or offer a lock instead
of vetoing the save.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import hou

from lore_dcc.errors import LoreError, NotAWorkingCopy
from lore_dcc.houdini import host, references
from lore_dcc.locks import LockState
from lore_dcc.session import FileState, Session
from lore_dcc.summaries import behind_offer_text

logger = logging.getLogger(__name__)

_TAG = "lore_dcc_hook"
_unknown_warned = False
_save_unlocked: set[str] = set()
_other_warned: set[str] = set()


def install() -> None:
    """Register the hip and asset callbacks once per Houdini session.

    Safe to call repeatedly; a failure is logged and never raised.
    """
    try:
        if not any(getattr(cb, _TAG, False) for cb in hou.hipFile.eventCallbacks()):
            hou.hipFile.addEventCallback(_on_hip_event)
        if not any(getattr(cb, _TAG, False) for _types, cb in hou.hda.eventCallbacks()):
            hou.hda.addEventCallback(
                (hou.hdaEventType.BeforeAssetSaved, hou.hdaEventType.AssetSaved),
                _on_asset_event,
            )
    except Exception:
        logger.exception("Lore hooks could not be installed")
    try:
        host.setup()
    except Exception:
        logger.exception("Lore could not set up its dialogs")


def _on_hip_event(event_type: Any) -> None:
    try:
        events = hou.hipFileEventType
        if event_type == events.BeforeSave:
            before_save(hou.hipFile.path())
        elif event_type == events.AfterSave:
            after_save(hou.hipFile.path())
        elif event_type == events.AfterLoad:
            after_load(hou.hipFile.path())
    except Exception:
        logger.exception("Lore hip hook failed")


def _on_asset_event(event_type: Any, asset_definition: Any = None, **_kwargs: Any) -> None:
    try:
        file = references.library_file(asset_definition)
        if file is None:
            return
        if event_type == hou.hdaEventType.BeforeAssetSaved:
            before_save(file)
        elif event_type == hou.hdaEventType.AssetSaved:
            after_save(file)
    except Exception:
        logger.exception("Lore asset hook failed")


setattr(_on_hip_event, _TAG, True)
setattr(_on_asset_event, _TAG, True)


def before_save(file: str) -> None:
    """Warn about, or offer a lock on, a lockable file before it is saved."""
    prefs = host.load_prefs()
    if not prefs.enabled:
        return
    session = _session(file)
    state = _state(session, file) if session else None
    if session is None or state is None or not state.lockable:
        return
    if state.lock is LockState.UNKNOWN:
        _warn_unknown(file)
    elif state.lock is LockState.LOCKED_BY_OTHER:
        _warn_locked_by_other(state, file)
    elif (
        state.lock is LockState.NOT_LOCKED
        and state.tracked
        and prefs.prompt_lock_before_save
        and _file_key(file) not in _save_unlocked
    ):
        _offer_lock(session, file)


def after_save(file: str) -> None:
    """Record a save with Lore and offer to stage a new file."""
    prefs = host.load_prefs()
    if not prefs.enabled:
        return
    session = _session(file)
    if session is None:
        return
    # Houdini fires AfterSave even when the write failed; marking an unchanged file is harmless.
    host.run(session.mark_saved, [file])
    if not prefs.prompt_stage_after_save:
        return
    state = _state(session, file)
    if (
        state is None
        or not state.exists
        or state.tracked
        or state.blocked
        or state.lock is LockState.LOCKED_BY_ME
    ):
        return
    if _is_staged(session, file):
        return
    choice = host.ask(
        "Lore - Stage New File?",
        f"{Path(file).name} is not staged in Lore. Stage it now?",
        ["Stage", "Not now"],
        default="Stage",
    )
    if choice == "Stage":
        if _file_key(file) == _file_key(hou.hipFile.path()):
            _remember_references(session, file, warn=True)
        _guarded(session.stage, [file], context=f"Staging {Path(file).name}")


def after_load(file: str) -> None:
    """Drop the cached identity of a freshly loaded file, then check its references.

    The server check runs in the background and asks only when something is behind.
    """
    if not host.load_prefs().enabled:
        return
    session = _session(file)
    if session is None:
        return
    session.invalidate()
    if host.has_ui():
        from lore_dcc.ui import tasks

        _remember_references(session, file, warn=False)
        tasks.run_in_background(
            session.behind, file, on_done=lambda report, error: _offer_sync(file, report, error)
        )


def _remember_references(session: Session, file: str, *, warn: bool) -> None:
    try:
        scene = references.scan(session.workspace.root)
        session.set_references(file, scene.inside)
        if warn:
            from lore_dcc.houdini import actions

            actions.warn_outside(scene)
    except Exception:
        logger.exception("Lore could not read the references of %s", file)


def _offer_sync(file: str, report: Any, error: BaseException | None) -> None:
    try:
        if error is not None:
            logger.warning("Could not check %s against the server: %s", file, error)
            return
        if _file_key(hou.hipFile.path()) != _file_key(file):
            return
        if not (report.behind or report.source_behind):
            return
        from lore_dcc.houdini import actions

        choice = host.ask(
            "Lore - Dependencies Behind Server",
            behind_offer_text(report),
            ["Sync Dependencies", "Not now"],
            default="Sync Dependencies",
        )
        if choice == "Sync Dependencies":
            actions.sync_dependencies(confirm=False)
    except Exception:
        logger.exception("Lore dependency check failed for %s", file)


def _session(file: str) -> Session | None:
    try:
        return host.session_for(file)
    except NotAWorkingCopy:
        return None
    except LoreError as exc:
        logger.warning("Lore is unavailable for %s: %s", file, exc)
        return None


def _state(session: Session, file: str) -> FileState | None:
    try:
        return host.run(session.file_states, [file])[0]
    except LoreError as exc:
        logger.warning("Could not read Lore state of %s: %s", file, exc)
        return None


def _is_staged(session: Session, file: str) -> bool:
    try:
        return bool(host.run(session.is_staged, file))
    except LoreError as exc:
        logger.warning("Could not read the staged files for %s: %s", file, exc)
        return False


def _file_key(file: str) -> str:
    return os.path.normcase(os.path.abspath(file))


def _guarded(call: Any, *args: Any, context: str) -> None:
    try:
        host.run(call, *args)
    except LoreError as exc:
        host.error(exc, context)


def _warn_unknown(file: str) -> None:
    global _unknown_warned
    logger.warning("Lock state of %s is unknown; it cannot be staged or committed", file)
    if _unknown_warned or not host.has_ui():
        return
    _unknown_warned = True
    host.message(
        "Lore - Lock State Unknown",
        "Lore could not tell who holds the lock on this file. The save goes ahead, but the "
        "plugin will not stage or commit it until the lock state is known. "
        "Run `lore login` or check the connection.",
    )


def _warn_locked_by_other(state: FileState, file: str) -> None:
    key = _file_key(file)
    logger.warning("%s is locked by %s", file, state.owner)
    if key in _other_warned or not host.has_ui():
        return
    _other_warned.add(key)
    host.message(
        "Lore - File Locked by Another User",
        f"{Path(file).name} is locked in Lore by {state.owner}. The save goes ahead, but the "
        "file cannot be staged or committed until they release the lock.",
    )


def _offer_lock(session: Session, file: str) -> None:
    name = Path(file).name
    choice = host.ask(
        "Lore - Lock File?",
        f"{name} is not locked in Lore. Lock it before saving?",
        ["Lock and save", "Save without lock"],
        default="Lock and save",
    )
    if choice == "Lock and save":
        _guarded(session.lock, [file], context=f"Locking {name}")
    elif choice == "Save without lock":
        _save_unlocked.add(_file_key(file))
