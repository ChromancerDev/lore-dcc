"""Save, load and asset-save hooks.

A hook never raises into Houdini. Houdini ignores an exception raised in a
Before callback and saves anyway, so the lock prompt offers choices instead of
a veto: a file left read-only simply fails in Houdini's own save.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import hou

from lore_dcc.errors import LoreError, NotAWorkingCopy
from lore_dcc.houdini import host
from lore_dcc.locks import LockState
from lore_dcc.session import FileState, Session

logger = logging.getLogger(__name__)

_TAG = "lore_dcc_hook"
_unknown_warned = False
_save_unlocked: set[str] = set()
_read_only_warned: set[str] = set()


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
        logger.exception("Lore could not set up its dialogs and prefs folder")


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
        file = library_file(asset_definition)
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


def library_file(definition: Any) -> str | None:
    """Return an HDA definition's library file, or None when it lives inside the hip."""
    if definition is None:
        return None
    file = definition.libraryFilePath()
    return file if file and file != "Embedded" and os.path.isabs(file) else None


def before_save(file: str) -> None:
    """Offer a lock or writable bit before a lockable file is saved."""
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
        _offer_writable(session, state, file)
    elif state.lock is LockState.NOT_LOCKED and state.tracked:
        _handle_unlocked(session, state, file, prefs.prompt_lock_before_save)


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
    if state is None or not state.exists or state.tracked or state.lock is LockState.LOCKED_BY_ME:
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
        _guarded(host.run, session.stage, [file], context=f"Staging {Path(file).name}")


def after_load(file: str) -> None:
    """Apply the read-only rule to a freshly loaded file. Never prompts."""
    if not host.load_prefs().enabled:
        return
    session = _session(file)
    if session is not None:
        session.invalidate()
        host.run(session.refresh_read_only, [file])


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
        call(*args)
    except LoreError as exc:
        host.error(exc, context)


def _warn_unknown(file: str) -> None:
    global _unknown_warned
    logger.warning("Lock state of %s is unknown; leaving it as it is", file)
    if _unknown_warned or not host.has_ui():
        return
    _unknown_warned = True
    host.message(
        "Lore - Lock State Unknown",
        "Lore could not tell who holds the lock on this file, so the plugin leaves it as it is "
        "and blocks committing it. Run `lore login` or check the connection.",
    )


def _offer_writable(session: Session, state: FileState, file: str) -> None:
    name = Path(file).name
    if os.access(file, os.W_OK):
        logger.info("%s is locked by %s and already writable", name, state.owner)
        return
    choice = host.ask(
        "Lore - File Locked by Another User",
        f"{name} is locked in Lore by {state.owner}. "
        "Saving here makes a local copy you cannot commit until they release the lock.",
        ["Make writable", "Leave read-only"],
        default="Leave read-only",
    )
    if choice == "Make writable":
        _guarded(host.run, session.make_writable, [file], context=f"Making {name} writable")


def _handle_unlocked(session: Session, state: FileState, file: str, prompt: bool) -> None:
    key = _file_key(file)
    read_only = state.exists and not os.access(file, os.W_OK)
    if key in _save_unlocked:
        if read_only:
            _guarded(host.run, session.make_writable, [file], context="Making the file writable")
    elif prompt:
        _offer_lock(session, file)
    elif read_only:
        _warn_read_only(file)


def _warn_read_only(file: str) -> None:
    name = Path(file).name
    logger.warning("%s is tracked and not locked, so saving it will fail", file)
    key = _file_key(file)
    if key in _read_only_warned or not host.has_ui():
        return
    _read_only_warned.add(key)
    host.message(
        "Lore - File Not Locked",
        f"{name} is tracked and not locked, so the save will fail. "
        "Use Lock on the Lore shelf, then save again.",
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
        _guarded(host.run, session.lock, [file], context=f"Locking {name}")
    elif choice == "Save without lock":
        _save_unlocked.add(_file_key(file))
        _guarded(host.run, session.make_writable, [file], context=f"Making {name} writable")
