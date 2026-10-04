"""Rows and texts for the Commit dialog. Pure Python, no Qt.

`build_rows` turns `lore status` plus per-file lock state into the ordered
rows the dialog shows. Everything is linear in the number of changed files, so
a repository with thousands of untracked entries stays responsive.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Protocol

from lore_dcc.backend.parsers import RemoteState, Status
from lore_dcc.locks import LockState, path_key
from lore_dcc.session import CommitResult, FileState

GROUP_SESSION = "This session"
GROUP_OUTPUTS = "Outputs"
GROUP_OTHER = "Other changes"
GROUP_BLOCKED = "Cannot commit"
GROUPS = (GROUP_SESSION, GROUP_OUTPUTS, GROUP_OTHER, GROUP_BLOCKED)

CODE_WORDS = {"M": "Modified", "A": "Added", "D": "Deleted"}

SUMMARY_HINT = "A summary is required."
FILES_HINT = "Select at least one file."

_REMOTE_TEXT = {
    RemoteState.IN_SYNC: "In sync with remote",
    RemoteState.BEHIND: "Behind remote",
    RemoteState.DIVERGED: "Diverged from remote",
    RemoteState.UNKNOWN: "Remote state unknown",
}


@dataclass(frozen=True)
class CommitRow:
    """One file in the Commit dialog.

    Attributes:
        path: Repo-relative path with forward slashes.
        code: Change code, `M`, `A` or `D`.
        group: One of GROUPS.
        checked: Default check state.
        enabled: False for files that cannot be committed.
        reason: Why the row is disabled, else an empty string.
        lock_owner: Lock owner as Lore prints it, if the file is locked.
        lock_text: Text for the lock column, empty when there is nothing to say.
    """

    path: str
    code: str
    group: str
    checked: bool
    enabled: bool
    reason: str
    lock_owner: str | None
    lock_text: str

    @property
    def status_word(self) -> str:
        """The change as a word: Modified, Added or Deleted."""
        return CODE_WORDS.get(self.code, self.code)


class CommitSource(Protocol):
    """The slice of Session the row builder reads."""

    touched: set[str]
    outputs: set[str]

    def status(self) -> Status: ...

    def file_states(self, paths: Sequence[Path | str]) -> list[FileState]: ...


def _merge_codes(a: str, b: str) -> str:
    if a == b:
        return a
    pair = {a, b}
    if pair == {"A", "D"}:
        return "M"
    return "A" if "A" in pair else "D"


def collect_changes(status: Status) -> dict[str, str]:
    """Return path -> change code for every changed file, in status order.

    Staged, unstaged and untracked entries all count; directory entries are
    skipped. A path in several sections gets one merged code.
    """
    codes: dict[str, str] = {}
    for change in chain(status.staged, status.unstaged, status.untracked):
        if change.is_dir:
            continue
        previous = codes.get(change.path)
        codes[change.path] = change.code if previous is None else _merge_codes(previous, change.code)
    return codes


def lock_text(state: FileState | None) -> str:
    """Return the lock column text for a file."""
    if state is None:
        return ""
    if state.lock is LockState.LOCKED_BY_ME:
        return "Locked by you"
    if state.lock is LockState.LOCKED_BY_OTHER:
        return f"Locked by {state.owner or 'another user'}"
    if state.lock is LockState.UNKNOWN and state.lockable:
        return "Lock state unknown"
    return ""


def build_rows(
    status: Status,
    states: Iterable[FileState],
    touched: Collection[str],
    outputs: Collection[str],
) -> list[CommitRow]:
    """Build the dialog rows, grouped in GROUPS order.

    Files already staged in `status` are checked by default in every group.

    Args:
        status: Parsed `lore status`.
        states: FileState for the changed paths. A path without one is treated
            as not lockable.
        touched: Paths this session locked, staged or saved.
        outputs: Paths the output flow wrote.

    Returns:
        Rows ordered by group, then by status order within the group. A file
        that cannot be committed appears only in "Cannot commit".
    """
    by_path = {path_key(state.path): state for state in states}
    touched_keys = {path_key(p) for p in touched}
    output_keys = {path_key(p) for p in outputs}
    staged_keys = {path_key(c.path) for c in status.staged if not c.is_dir}
    buckets: dict[str, list[CommitRow]] = {group: [] for group in GROUPS}
    for path, code in collect_changes(status).items():
        key = path_key(path)
        state = by_path.get(key)
        owner = state.owner if state else None
        text = lock_text(state)
        if state is not None and state.blocked:
            reason = text if state.lock is LockState.LOCKED_BY_OTHER else "Lock state unknown"
            row = CommitRow(path, code, GROUP_BLOCKED, False, False, reason, owner, reason)
            buckets[GROUP_BLOCKED].append(row)
            continue
        if key in touched_keys:
            group, checked = GROUP_SESSION, True
        elif key in output_keys:
            group, checked = GROUP_OUTPUTS, True
        else:
            group, checked = GROUP_OTHER, False
        checked = checked or key in staged_keys
        buckets[group].append(CommitRow(path, code, group, checked, True, "", owner, text))
    return [row for group in GROUPS for row in buckets[group]]


def load_rows(session: CommitSource) -> tuple[Status, list[CommitRow]]:
    """Read status and file states from a session and build the rows."""
    status = session.status()
    paths = list(collect_changes(status))
    states = session.file_states(paths) if paths else []
    return status, build_rows(status, states, session.touched, session.outputs)


def remote_state_text(state: RemoteState) -> str:
    """Return the display text for a remote state."""
    return _REMOTE_TEXT[state]


def files_label(count: int) -> str:
    """Return the footer text, e.g. `1 file` or `3 files`."""
    return f"{count} file" if count == 1 else f"{count} files"


def commit_hint(summary: str, checked_count: int) -> str:
    """Return why Commit is disabled, or an empty string when it is allowed."""
    if not summary.strip():
        return SUMMARY_HINT
    if checked_count == 0:
        return FILES_HINT
    return ""


def first_error_line(text: str | None) -> str:
    """Return the first non-blank line of Lore output without a trailing period."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip().rstrip(".")
    return "unknown error"


def commit_complete_text(result: CommitResult) -> str:
    """Return the Commit Complete message for a commit that landed, pushed or not.

    A revision, branch or server revision Lore did not report is left out of
    the text rather than guessed.
    """
    number = result.revision if result.revision is not None else result.pushed_revision
    revision = f"revision {number}" if number is not None else "your changes"
    if result.pushed:
        target = f" to {result.branch}" if result.branch else ""
        lines = [f"Committed {revision} and pushed it{target}."]
    else:
        lines = [
            (
                f"Committed {revision} locally. "
                "It has not been pushed, so nothing has reached the server yet."
            )
        ]
    if result.server_revision is not None:
        lines.append(f"The server is at revision {result.server_revision}.")
    return "\n".join(lines)


def push_failure_text(result: CommitResult) -> str:
    """Return the message for a commit that landed locally but was not pushed."""
    if result.diverged:
        return (
            "Committed locally. The branch is behind remote, so the push was refused. "
            "Your commit is safe locally and nothing has reached the server yet. "
            "Sync outside this app with `lore sync`, then retry the push."
        )
    return (
        f"Committed locally. Push failed: {first_error_line(result.push_error)}. "
        "Nothing has reached the server yet."
    )
