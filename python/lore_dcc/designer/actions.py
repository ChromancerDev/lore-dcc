"""Lore menu and Explorer toolbar actions.

Each public function is one menu or toolbar entry. Most work on the current
package: the package of the graph in focus. Errors reach the user through
`host.error`; nothing here raises to Designer.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lore_dcc.designer import host, packages, references
from lore_dcc.errors import LoreError, NotAWorkingCopy
from lore_dcc.locks import path_key
from lore_dcc.session import Session, new_file_message
from lore_dcc.summaries import list_paths, outside_text, scene_behind_text, sync_summary

logger = logging.getLogger(__name__)

TITLE = "Lore"


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
            "Lore is turned off. Use Enable/Disable in the Lore menu to turn it on.",
        )
        return None
    try:
        return host.session_for(file)
    except NotAWorkingCopy:
        host.message("Lore - Not a Working Copy", "Not a Lore working copy")
        return None


def _package_file() -> str | None:
    """Return the saved file of the current package, or None after telling the user why."""
    package = packages.current()
    if package is None:
        host.message("Lore - No Package", "Open a graph of the package first.")
        return None
    file = packages.file_of(package)
    if file is None:
        host.message("Lore - Save File First", "Save the package first.")
    return file


def _rel(session: Session, file: Path | str) -> str:
    return session.workspace.relative(Path(file))


def _scan(session: Session, file: str, *, warn: bool) -> references.SceneReferences | None:
    """Remember the references of the open package at `file`; None when it is not open."""
    package = packages.find(file)
    if package is None:
        return None
    scene = references.scan(package, session.workspace.root)
    session.set_references(file, scene.inside)
    if warn:
        warn_outside(scene)
    return scene


@_reported
def info() -> None:
    """Show working copy, branch, identity and remote for the current package."""
    from lore_dcc.ui import dialogs

    file = _package_file()
    session = _session(file) if file else None
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
def file_a_bug() -> None:
    """Open a prefilled bug report on GitHub in the default browser."""
    from lore_dcc import bugs

    if not bugs.open_report("Designer", host.app().getVersion()):
        host.message("Lore - File a Bug", f"Open this link to file a bug:\n{bugs.NEW_ISSUE}")


@_reported
def lock() -> None:
    """Lock the current package."""
    file = _package_file()
    session = _session(file) if file else None
    if file and session:
        _lock_files(session, [file])


@_reported
def discard() -> None:
    """Discard changes to the current package, then reload it."""
    file = _package_file()
    session = _session(file) if file else None
    if file and session and _discard_files(session, [file], "Unsaved changes are lost."):
        packages.reload(file, discard_edits=True)


@_reported
def lock_selection(explorer_id: int) -> None:
    """Lock what is selected in the Explorer, or the current package when nothing is."""
    files = _selected_files(explorer_id)
    session = _session(files[0]) if files else None
    if files and session:
        _lock_files(session, files)


@_reported
def discard_selection(explorer_id: int) -> None:
    """Discard changes to what is selected in the Explorer, then reload open packages."""
    files = _selected_files(explorer_id)
    session = _session(files[0]) if files else None
    if not (files and session):
        return
    if _discard_files(session, files, "Unsaved changes to these packages are lost."):
        for file in files:
            packages.reload(file, discard_edits=True)
        _, pending = references.reload([Path(f) for f in files if packages.find(f) is None])
        if pending:
            host.message("Lore - Discard Changes", _reopen_text(pending))


def _selected_files(explorer_id: int) -> list[str]:
    """Return the files of the selected packages and linked resources, or the current package."""
    items = list(host.qt_ui().getExplorerSelection(explorer_id) or [])
    if not items:
        file = _package_file()
        return [file] if file else []
    files: list[str] = []
    for item in items:
        file = _item_file(item)
        if file and path_key(file) not in {path_key(f) for f in files}:
            files.append(file)
    if not files:
        host.message(
            "Lore - Explorer Selection",
            "Select a saved package or a linked resource, such as a linked bitmap.",
        )
    return files


def _item_file(item: Any) -> str | None:
    """Return a linked resource's file, else the file of the package holding the item."""
    if hasattr(item, "getDependencies"):
        return packages.file_of(item)
    if hasattr(item, "getEmbedMethod") and (file := references.linked_file(item)):
        return file
    return packages.file_of(item.getPackage()) if hasattr(item, "getPackage") else None


@_reported
def sync_dependencies(confirm: bool = True, file: str | None = None) -> None:
    """Sync the files the current package reads that are newer on the server.

    `file` names the package to check instead of the current one. `confirm=False`
    skips the question before syncing references only, for callers that
    already asked. A package behind the server always asks.
    """
    file = file or _package_file()
    session = _session(file) if file else None
    if not (file and session) or _scan(session, file, warn=False) is None:
        return
    report = host.run(session.behind, file, title="Checking the server")
    if not (report.behind or report.source_behind):
        host.message("Lore - Sync Dependencies", "Every reference is up to date with the server.")
        return
    reopen = False
    if report.source_behind:
        edited = _has_local_edits(session, file)
        choice = host.ask(
            "Lore - Scene Behind Server",
            scene_behind_text(report, edited),
            ["Continue", "Cancel"] if edited else ["Continue", "Sync and reopen", "Cancel"],
            default="Continue" if edited else "Sync and reopen",
        )
        if choice == "Continue" and not report.behind:
            host.message("Lore - Sync Dependencies", "No references are behind the server.")
            return
        reopen = choice == "Sync and reopen"
        if not (reopen or choice == "Continue"):
            return
        if reopen and packages.is_modified(file):
            answer = host.ask(
                "Lore - Discard Unsaved Changes?",
                "Syncing reopens the package and drops its unsaved changes.",
                ["Sync and reopen", "Cancel"],
                default="Cancel",
            )
            if answer != "Sync and reopen":
                return
    elif confirm:
        choice = host.ask(
            "Lore - Sync Dependencies?",
            f"These references are newer on the server:\n{list_paths(report.behind)}",
            ["Sync", "Cancel"],
            default="Sync",
        )
        if choice != "Sync":
            return
    own = path_key(_rel(session, file))
    roots = [*report.behind, *([_rel(session, file)] if reopen else [])]
    result = host.run(session.sync_references, roots, title="Syncing")
    others = [session.workspace.root / p for p in result.synced if path_key(p) != own]
    reloaded, pending = references.reload(others)
    reopened = reopen and any(path_key(p) == own for p in result.synced)
    if reopened:
        packages.reload(file, discard_edits=True)
    text = sync_summary(result, reopen and not reopened)
    if reloaded or reopened:
        text = f"{text}\n\n{RELOADED_TEXT}"
    if pending:
        text = f"{text}\n\n{_reopen_text([session.workspace.relative(p) for p in pending])}"
    host.message("Lore - Sync Dependencies", text)


RELOADED_TEXT = (
    "Designer reloaded the open packages that use the synced packages and closed their graph "
    "views. Open the graphs again from the Explorer."
)


def _reopen_text(paths: list[Any]) -> str:
    return (
        "Designer could not reload these files, because they are not bitmaps or a package "
        "that uses them has unsaved changes. Save, then close and reopen the package to see "
        f"them:\n{list_paths([str(p) for p in paths])}"
    )


@_reported
def commit() -> None:
    """Open the Commit dialog with the current package's references."""
    from lore_dcc.ui import dialogs

    file = _package_file()
    session = _session(file) if file else None
    if session:
        _scan(session, file, warn=True)
        dialogs.show_commit(session, host.load_prefs())


def warn_outside(scene: references.SceneReferences) -> None:
    """Tell the user which referenced files live outside the working copy."""
    if scene.outside:
        host.message("Lore - References Outside the Repo", outside_text(scene.outside))


def _has_local_edits(session: Session, file: str) -> bool:
    status = host.run(session.lore.check_dirty, [Path(file)], title="Checking the package")
    key = path_key(_rel(session, file))
    return any(path_key(change.path) == key for change in (*status.staged, *status.unstaged))


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
