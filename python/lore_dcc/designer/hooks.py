"""Package save and load hooks.

A hook never raises into Designer. The callbacks only decode the path and
defer the work to Designer's event loop with `host.later`, since Designer
fires them before its package list is up to date.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from lore_dcc.designer import host, packages, references
from lore_dcc.errors import LoreError, NotAWorkingCopy
from lore_dcc.locks import LockState
from lore_dcc.session import FileState, Session
from lore_dcc.summaries import behind_offer_text
from lore_dcc.workspace import find_workspace

logger = logging.getLogger(__name__)

_callback_ids: list[int] = []
_unknown_warned = False
_save_unlocked: set[str] = set()
_other_warned: set[str] = set()


def install() -> None:
    """Register the package callbacks once; a failure is logged and never raised."""
    if _callback_ids:
        return
    try:
        app = host.app()
        _callback_ids.extend(
            (
                app.registerBeforeFileSavedCallback(_on_before_save),
                app.registerAfterFileSavedCallback(_on_after_save),
                app.registerAfterFileLoadedCallback(_on_after_load),
            )
        )
    except Exception:
        logger.exception("Lore hooks could not be installed")


def uninstall() -> None:
    """Unregister the package callbacks."""
    app = host.app()
    while _callback_ids:
        try:
            app.unregisterCallback(_callback_ids.pop())
        except Exception:
            logger.exception("Lore could not unregister a callback")


def _decode(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value or "")


def _on_before_save(target: Any, _previous: Any) -> None:
    try:
        file = _decode(target)
        if file:
            before_save(file)
    except Exception:
        logger.exception("Lore before-save hook failed")


def _on_after_save(path: Any, succeeded: bool) -> None:
    try:
        file = _decode(path)
        if file and succeeded:
            host.later(lambda: _guard(after_save, file))
    except Exception:
        logger.exception("Lore after-save hook failed")


def _on_after_load(path: Any, *_flags: Any) -> None:
    try:
        file = _decode(path)
        if file:
            host.later(lambda: _guard(after_load, file))
    except Exception:
        logger.exception("Lore after-load hook failed")


def _guard(step: Any, file: str) -> None:
    try:
        step(file)
    except Exception:
        logger.exception("Lore hook failed on %s", file)


def before_save(file: str) -> None:
    """Warn about, or offer a lock on, a lockable package before it is saved."""
    prefs = host.load_prefs()
    if not prefs.enabled or not os.path.isfile(file):
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
    """Record a save with Lore and offer to stage a new package."""
    prefs = host.load_prefs()
    if not prefs.enabled:
        return
    session = _session(file)
    if session is None:
        return
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
        _remember_references(session, file, warn=True)
        _guarded(session.stage, [file], context=f"Staging {Path(file).name}")


def after_load(file: str) -> None:
    """Drop the cached identity, then check the package's references in the background.

    The server check asks only when something is behind.
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
        package = packages.find(file)
        if package is None:
            return
        scene = references.scan(package, session.workspace.root)
        session.set_references(file, scene.inside)
        if warn:
            from lore_dcc.designer import actions

            actions.warn_outside(scene)
    except Exception:
        logger.exception("Lore could not read the references of %s", file)


def _offer_sync(file: str, report: Any, error: BaseException | None) -> None:
    try:
        if error is not None:
            logger.warning("Could not check %s against the server: %s", file, error)
            return
        if packages.find(file) is None or not (report.behind or report.source_behind):
            return
        from lore_dcc.designer import actions

        choice = host.ask(
            "Lore - Dependencies Behind Server",
            f"{Path(file).name}\n\n{behind_offer_text(report)}",
            ["Sync Dependencies", "Not now"],
            default="Sync Dependencies",
        )
        if choice == "Sync Dependencies":
            actions.sync_dependencies(confirm=False, file=file)
    except Exception:
        logger.exception("Lore dependency check failed for %s", file)


def _session(file: str) -> Session | None:
    """Return the Session for `file`. The folder walk comes first, so files outside
    every working copy, such as Designer's own library, never wait on Lore."""
    try:
        find_workspace(Path(file))
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
