"""Menu and context-menu actions, each a flow (see `flow`).

Every public function returns a generator for `host.start`. Errors reach the
user as an `Error` popup; nothing here raises to Blender. Flow code runs on
Blender's main thread, so it may read bpy; only `Run` work goes to the worker.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
import os
import re
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bpy
from lore_dcc import commit_model
from lore_dcc.backend.parsers import Status
from lore_dcc.blender import host, outputs, references
from lore_dcc.blender.flow import Ask, Error, Flow, Invoke, Message, Run
from lore_dcc.commit_model import CommitRow
from lore_dcc.errors import LoreCommandError, LoreError, NotAWorkingCopy
from lore_dcc.locks import LockState, path_key
from lore_dcc.references import SceneReferences
from lore_dcc.session import Session, new_file_message
from lore_dcc.summaries import (
    describe_write,
    list_paths,
    outside_text,
    scene_behind_text,
    sync_summary,
    with_notes,
)

logger = logging.getLogger(__name__)

TITLE = "Lore"
BACKUP = re.compile(r"\.blend\d+$", re.IGNORECASE)

Sub = Generator[Any, Any, Any]


def reported(func: Callable[..., Sub]) -> Callable[..., Flow]:
    """Turn an action's errors into an Error popup instead of ending the flow silently."""

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Flow:
        try:
            yield from func(*args, **kwargs)
        except LoreError as exc:
            yield Error(exc, TITLE)
        except Exception as exc:
            logger.exception("Lore action %s failed", func.__name__)
            yield Error(exc, TITLE)

    return wrapper


def blend_file() -> str:
    """Return the open .blend's path, or an empty string when it was never saved."""
    return bpy.data.filepath


def _session(file: Path | str) -> Sub:
    """Return the Session for `file`, telling the user why when there is none."""
    if not host.load_prefs().enabled:
        yield Message(
            "Lore - Disabled",
            "Lore is turned off. Use Enable/Disable in the Lore menu to turn it on.",
        )
        return None
    try:
        return (yield Run(host.session_for, file, title="Reading the working copy"))
    except NotAWorkingCopy:
        yield Message("Lore - Not a Working Copy", "Not a Lore working copy")
        return None


def _saved_blend() -> Sub:
    file = blend_file()
    if not file or not os.path.isfile(file):
        yield Message("Lore - Save File First", "Save the file first.")
        return None
    return file


def _rel(session: Session, file: Path | str) -> str:
    return session.workspace.relative(Path(file))


def _is_blend(file: Path | str) -> bool:
    return bool(blend_file()) and _key(file) == _key(blend_file())


def _key(file: Path | str) -> str:
    return os.path.normcase(os.path.abspath(file))


@dataclass(frozen=True)
class InfoData:
    """What the Info popup shows."""

    status: Status
    identity_text: str
    version_text: str


def load_info(session: Session) -> InfoData:
    """Read status, identity and version. Runs on the worker."""
    status = session.status()
    try:
        identity_text = session.lore.auth_info().name
    except LoreCommandError:
        hint = "lore login"
        if session.workspace.remote_url:
            hint = f"lore login {session.workspace.remote_url}"
        identity_text = f"Not signed in. Run in a terminal: {hint}"
    try:
        major, minor, patch = session.lore.version()
        version_text = f"{major}.{minor}.{patch}"
    except LoreError:
        version_text = "Unknown"
    return InfoData(status, identity_text, version_text)


def info_text(session: Session, data: InfoData) -> str:
    """Return the Info popup body."""
    status = data.status
    rows = (
        ("Working copy", str(session.workspace.root)),
        ("Branch", status.branch),
        ("Revision", str(status.revision)),
        ("Remote state", commit_model.remote_state_text(status.remote_state)),
        ("Remote URL", session.workspace.remote_url or "Not set"),
        ("Identity", data.identity_text),
        ("Lore version", data.version_text),
    )
    return "\n".join(f"{name}: {value}" for name, value in rows)


@reported
def info() -> Sub:
    """Show working copy, branch, identity and remote for the open file."""
    file = blend_file()
    if not file:
        yield Message("Lore - Save File First", "Save the file inside a Lore working copy first.")
        return
    session = yield from _session(file)
    if session:
        data = yield Run(load_info, session, title="Reading Lore info")
        yield Message("Lore - Info", info_text(session, data))


@reported
def toggle_enabled() -> Sub:
    """Turn the Lore hooks on or off for this user."""
    prefs = host.load_prefs()
    prefs = dataclasses.replace(prefs, enabled=not prefs.enabled)
    host.save_prefs(prefs)
    if prefs.enabled:
        yield Message("Lore - Enabled", "Lore is now enabled.")
    else:
        yield Message("Lore - Disabled", "Lore is now disabled.")


@reported
def file_a_bug() -> Sub:
    """Open a prefilled bug report on GitHub in the default browser."""
    from lore_dcc import bugs

    if not bugs.open_report("Blender", bpy.app.version_string):
        yield Message("Lore - File a Bug", f"Open this link to file a bug:\n{bugs.NEW_ISSUE}")


@reported
def lock() -> Sub:
    """Lock the open .blend."""
    file = yield from _saved_blend()
    session = (yield from _session(file)) if file else None
    if file and session:
        yield from _lock_files(session, [file])


@reported
def discard() -> Sub:
    """Discard changes to the open .blend, then reopen it."""
    file = yield from _saved_blend()
    session = (yield from _session(file)) if file else None
    if not (file and session):
        return
    if (yield from _discard_files(session, [file], "Unsaved changes in this session are lost.")):
        yield Invoke("wm.revert_mainfile", {}, mode="EXEC_DEFAULT")


@reported
def lock_files(files: Sequence[str]) -> Sub:
    """Lock files the scene reads, such as linked libraries or images."""
    files = yield from _existing(files)
    session = (yield from _session(files[0])) if files else None
    if session:
        yield from _lock_files(session, files)


@reported
def discard_files(files: Sequence[str]) -> Sub:
    """Discard changes to files the scene reads, then reload them in Blender."""
    files = yield from _existing(files)
    session = (yield from _session(files[0])) if files else None
    if not session:
        return
    blend = any(_is_blend(f) for f in files)
    warning = "Unsaved changes in this session are lost." if blend else ""
    if not (yield from _discard_files(session, files, warning)):
        return
    if blend:
        yield Invoke("wm.revert_mainfile", {}, mode="EXEC_DEFAULT")
        return
    stale = references.reload(files)
    if stale:
        yield Message("Lore - Discard Changes", _stale_text(stale))


@reported
def collect_outputs() -> Sub:
    """Scan the scene for files it writes and report them to Lore."""
    file = yield from _saved_blend()
    session = (yield from _session(file)) if file else None
    if session:
        notes: list[str] = []
        paths = outputs.scene_outputs(bpy.context.scene, session.workspace.root, notes)
        yield from _collect(session, paths, notes)


@reported
def lock_outputs() -> Sub:
    """Lock the tracked files the scene's render is about to write."""
    file = yield from _saved_blend()
    session = (yield from _session(file)) if file else None
    if not session:
        return
    notes: list[str] = []
    paths = outputs.scene_outputs(bpy.context.scene, session.workspace.root, notes)
    locked = (yield Run(session.prepare_write, paths, title="Locking outputs")) if paths else ()
    if locked:
        text = f"Locked {len(locked)} output file(s)."
    elif paths and (yield from _all_mine(session, paths)):
        text = "Already locked by you."
    else:
        text = "Nothing to lock. New files need no lock."
    yield Message("Lore - Lock Outputs", with_notes(text, notes))


@reported
def sync_dependencies(confirm: bool = True) -> Sub:
    """Sync the files the open .blend reads that are newer on the server.

    `confirm=False` skips the question before syncing references only, for
    callers that already asked. A .blend behind the server always asks.
    """
    file = yield from _saved_blend()
    session = (yield from _session(file)) if file else None
    if not (file and session):
        return
    scene = references.scan(session.workspace.root)
    session.set_references(file, scene.inside)
    report = yield Run(session.behind, file, title="Checking the server")
    if not (report.behind or report.source_behind):
        yield Message("Lore - Sync Dependencies", "Every reference is up to date with the server.")
        return
    reopen = False
    if report.source_behind:
        edited = yield from _has_local_edits(session, file)
        choice = yield Ask(
            "Lore - Scene Behind Server",
            scene_behind_text(report, edited),
            ["Continue", "Cancel"] if edited else ["Continue", "Sync and reopen", "Cancel"],
            "Continue" if edited else "Sync and reopen",
        )
        if choice == "Continue" and not report.behind:
            yield Message("Lore - Sync Dependencies", "No references are behind the server.")
            return
        reopen = choice == "Sync and reopen"
        if not (reopen or choice == "Continue"):
            return
        if reopen and bpy.data.is_dirty:
            answer = yield Ask(
                "Lore - Discard Unsaved Changes?",
                "Syncing reopens the scene and drops its unsaved changes.",
                ["Sync and reopen", "Cancel"],
                "Cancel",
            )
            if answer != "Sync and reopen":
                return
    elif confirm:
        choice = yield Ask(
            "Lore - Sync Dependencies?",
            f"These references are newer on the server:\n{list_paths(report.behind)}",
            ["Sync", "Cancel"],
            "Sync",
        )
        if choice != "Sync":
            return
    blend_key = path_key(_rel(session, file))
    roots = [*report.behind, *([_rel(session, file)] if reopen else [])]
    result = yield Run(session.sync_references, roots, title="Syncing")
    others = [session.workspace.root / p for p in result.synced if path_key(p) != blend_key]
    reopened = reopen and any(path_key(p) == blend_key for p in result.synced)
    stale = references.reload(others) if others and not reopened else []
    text = sync_summary(result, reopen and not reopened)
    if stale:
        text = f"{text}\n\n{_stale_text(stale)}"
    if reopened:
        yield Invoke("wm.revert_mainfile", {}, mode="EXEC_DEFAULT")
    yield Message("Lore - Sync Dependencies", text)


@reported
def commit() -> Sub:
    """Read the changes and open the Commit dialog."""
    file = blend_file()
    if not file:
        yield Message("Lore - Save File First", "Save the file inside a Lore working copy first.")
        return
    session = yield from _session(file)
    if not session:
        return
    if os.path.isfile(file):
        scene = references.scan(session.workspace.root)
        session.set_references(file, scene.inside)
        yield from warn_outside(scene)
    status, rows = yield Run(commit_model.load_rows, session, title="Reading changes")
    rows = commit_rows(rows)
    if not rows:
        yield Message("Lore - Commit", "There are no changes to commit.")
        return
    from lore_dcc.blender import commit_ui

    commit_ui.prepare(session, status, rows, host.load_prefs())
    yield Invoke("lore.commit_dialog")


def commit_rows(rows: Sequence[CommitRow]) -> list[CommitRow]:
    """Drop Blender's numbered backups, such as `shot.blend1`; Lore never tracks them."""
    return [row for row in rows if not BACKUP.search(row.path)]


@reported
def run_commit(
    session: Session, include: list[str], summary: str, description: str, push: bool, keep: bool
) -> Sub:
    """Commit from the dialog, offer Retry push while the push fails, then report."""
    result = yield Run(
        session.commit, include, summary, description, push, keep, title="Committing"
    )
    while not result.pushed and (push or result.push_error is not None):
        choice = yield Ask(
            "Lore - Push Failed",
            commit_model.push_failure_text(result),
            ["Retry push", "Close"],
            "Retry push",
        )
        if choice != "Retry push":
            return
        try:
            retried = yield Run(session.retry_push, title="Pushing")
        except LoreError as exc:
            yield Error(exc, "Push failed")
            continue
        if retried.revision is None:
            retried = dataclasses.replace(retried, revision=result.revision)
        result = retried
    yield Message("Lore - Commit Complete", commit_model.commit_complete_text(result))


def warn_outside(scene: SceneReferences) -> Sub:
    """Tell the user which referenced files live outside the working copy."""
    if scene.outside:
        yield Message("Lore - References Outside the Repo", outside_text(scene.outside))


def _existing(files: Sequence[str]) -> Sub:
    """Return the files that exist, telling the user about the rest."""
    found: list[str] = []
    problems: list[str] = []
    for file in files:
        if not file.strip():
            problems.append("The path is empty. Set it to a file first.")
        elif os.path.isdir(file):
            problems.append(f"{file} is a folder. Pick a file.")
        elif not os.path.isfile(file):
            problems.append(f"{file} does not exist.")
        else:
            found.append(file)
    if problems:
        yield Message("Lore - File", "\n".join(problems))
    return found if not problems else []


def _stale_text(labels: Sequence[str]) -> str:
    return (
        "Blender cannot reload these while the file is open. "
        f"Reopen the file to see the new versions:\n{list_paths(labels)}"
    )


def _has_local_edits(session: Session, file: str) -> Sub:
    status = yield Run(session.lore.check_dirty, [Path(file)], title="Checking the scene")
    key = path_key(_rel(session, file))
    return any(path_key(change.path) == key for change in (*status.staged, *status.unstaged))


def _all_mine(session: Session, paths: list[Path]) -> Sub:
    states = yield Run(session.file_states, paths)
    held = [s for s in states if s.tracked and s.lockable]
    return bool(held) and all(s.lock is LockState.LOCKED_BY_ME for s in held)


def _collect(session: Session, paths: list[Path], notes: list[str]) -> Sub:
    if not paths:
        yield Message(
            "Lore - Collect Outputs",
            with_notes("This scene writes no files inside the working copy.", notes),
        )
        return
    result = yield Run(session.finish_write, paths, title="Collecting outputs")
    yield Message("Lore - Collect Outputs", with_notes(describe_write(result), notes))


def _lock_files(session: Session, files: list[str]) -> Sub:
    yield Run(session.lock, files, title="Locking")
    names = ", ".join(_rel(session, f) for f in files)
    yield Message("Lore - Lock File", f"Locked {names}.")


def _discard_files(session: Session, files: list[str], warning: str) -> Sub:
    states = yield Run(session.file_states, files)
    new = [s.path for s in states if not s.tracked]
    if new:
        yield Message("Lore - Discard Changes", "\n".join(new_file_message(path) for path in new))
        return False
    names = "\n".join(f"    {_rel(session, f)}" for f in files)
    parts = [f"Discard changes to:\n{names}", warning, "This cannot be undone."]
    text = "\n\n".join(part for part in parts if part)
    choice = yield Ask("Lore - Discard Changes?", text, ["Discard changes", "Cancel"], "Cancel")
    if choice != "Discard changes":
        return False
    yield Run(session.discard, files, title="Discarding changes")
    return True
