"""Parsers for Lore CLI text output.

Lore has no machine-readable output, so each parser accepts only the lines it
knows and raises LoreOutputError on anything else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from lore_dcc.errors import LoreOutputError
from lore_dcc.locks import LockRecord

_VERSION = re.compile(r"^lore (?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:\+\S+)?$")
_REPOSITORY = re.compile(r"^Repository (?P<id>[0-9a-f]+)$")
_ON_BRANCH = re.compile(r"^On branch (?P<branch>\S+) revision (?P<rev>\d+) -> (?P<sig>[0-9a-f]+)$")
_REMOTE_REVISION = re.compile(r"^Remote revision (?P<rev>\d+) -> (?P<sig>[0-9a-f]+)$")
_CHANGE = re.compile(r"^(?P<code>[MAD]) (?P<path>.+)$")
# `status --check-dirty` and `--scan` end with counts; only "added" and "deleted" have been captured.
_TRACKED_COUNT = re.compile(r"^Tracked changes: \d+ (added|deleted)(, \d+ (added|deleted))*$")
_LOCK = re.compile(r"^(?P<path>.+) by (?P<owner>.+?) on branch (?P<branch>\S+)$")
_BRANCH = re.compile(r"^Branch (?P<name>\S.*)$")
_COMMITTED_REVISION = re.compile(r"^Revision\s*:\s*(?P<rev>\d+)$")
_PUSHED_REVISION = re.compile(
    r"^Pushed revision (?P<rev>\d+) -> (?P<sig>[0-9a-f]+) to branch (?P<branch>\S+)$"
)
_DEPENDENCY = re.compile(r"^  (?P<path>\S.*?) \[(?P<tags>[^\]]*)\]$")
_CONNECTION_FAILED = re.compile(r"^\[Warn\] Connection to (?P<url>\S+) failed$")
_REMOTE_UNREADABLE = re.compile(r"^Remote reachable but could not read remote revision \(.*\)$")
_SYNC_BLOCKED = re.compile(r"^\[Error\] File has local changes: (?P<path>.+?) \(incoming size .*\)$")
BRANCH_INFO_KEYS = ("ID", "Latest", "Remote Latest", "Creator", "Created")


class RemoteState(Enum):
    """How the local branch relates to the remote, from the `Local branch` line."""

    IN_SYNC = "in_sync"
    BEHIND = "behind"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"
    UNREACHABLE = "unreachable"


_REMOTE_NOTES = {
    "Local branch in sync with remote": RemoteState.IN_SYNC,
    "Local branch is behind remote": RemoteState.BEHIND,
    "Local branch has diverged, synchronize to merge": RemoteState.DIVERGED,
}

FILE_INFO_KEYS = ("Path", "Type", "Size", "Mode", "Context", "Hash", "Status")
FILE_STATUS_TRACKED = frozenset({"-", "Modified", "Deleted"})
FILE_STATUS_VALUES = FILE_STATUS_TRACKED | {"Added"}
_LINK_SIZE = re.compile(r"^(dependencies|dependents):\s+<\d+ bytes>$")

NO_CHANGES_LINE = "No tracked changes"

LOCK_HELD_HEADERS = ("Lock acquired on files:", "Lock already owned on files:")

_SECTIONS = {
    "Changes staged for commit:": "staged",
    "Staged changes:": "staged",
    "Changes not staged for commit:": "unstaged",
    "Untracked files:": "untracked",
}


@dataclass(frozen=True)
class Change:
    """One entry in a status section.

    Attributes:
        code: `M`, `A` or `D`.
        path: Repo-relative path with forward slashes, no trailing slash.
        is_dir: True when Lore printed the path with a trailing slash.
    """

    code: str
    path: str
    is_dir: bool


@dataclass(frozen=True)
class Status:
    """Parsed `lore status` output.

    Attributes:
        branch: The current branch.
        revision: The local revision number.
        remote_revision: The remote revision number, or None when the remote
            line is missing, which Lore does silently when the server is down.
        remote_note: The `Local branch ...` line as printed, if any.
        staged: Changes staged for commit.
        unstaged: Tracked changes not staged.
        untracked: Files Lore does not track yet.
        signature: The local revision signature from the `On branch` line.
        remote_signature: The remote revision signature, or None with the
            remote line missing.
    """

    branch: str
    revision: int
    remote_revision: int | None
    remote_note: str | None
    staged: tuple[Change, ...]
    unstaged: tuple[Change, ...]
    untracked: tuple[Change, ...]
    signature: str | None = None
    remote_signature: str | None = None
    unreachable: bool = False

    @property
    def remote_state(self) -> RemoteState:
        """The remote state: UNREACHABLE when Lore could not reach the server,
        UNKNOWN when the line is missing or unfamiliar."""
        if self.unreachable:
            return RemoteState.UNREACHABLE
        return _REMOTE_NOTES.get(self.remote_note or "", RemoteState.UNKNOWN)


@dataclass(frozen=True)
class AuthInfo:
    """Parsed `lore auth info` output."""

    user_id: str
    name: str


@dataclass(frozen=True)
class FileInfo:
    """One block of `lore file info` output.

    Attributes:
        path: Repo-relative path as Lore printed it.
        status: `-` (tracked, clean), `Modified`, `Deleted` or `Added` (not tracked).
    """

    path: str
    status: str

    @property
    def tracked(self) -> bool:
        """True when the current revision has the file."""
        return self.status in FILE_STATUS_TRACKED


@dataclass(frozen=True)
class BranchInfo:
    """Parsed `lore branch info` output.

    Attributes:
        name: The branch name.
        id: The branch ID, as lock records print it.
    """

    name: str
    id: str


@dataclass(frozen=True)
class PushedRevision:
    """The `Pushed revision N -> <signature> to branch <name>` line of `lore push`.

    Attributes:
        revision: The pushed revision number.
        branch: The branch name the revision went to.
    """

    revision: int
    branch: str


def committed_revision(text: str) -> int | None:
    """Return the revision number from `lore commit` output, or None when absent.

    Reads the `Revision  : N` line. It feeds a message, so it never raises.
    """
    for line in text.splitlines():
        if match := _COMMITTED_REVISION.match(line.strip()):
            return int(match["rev"])
    return None


def pushed_revision(text: str) -> PushedRevision | None:
    """Return the last pushed revision in `lore push` output, or None when absent.

    It feeds a message, so it never raises.
    """
    found: PushedRevision | None = None
    for line in text.splitlines():
        if match := _PUSHED_REVISION.match(line.strip()):
            found = PushedRevision(int(match["rev"]), match["branch"])
    return found


def parse_revision_diff(text: str) -> tuple[Change, ...]:
    """Parse `lore revision diff <sig> --target <sig>`: one `M|A|D <path>` line per change.

    Blank output means no change. Any other line raises.
    """
    changes = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _CHANGE.match(line.strip())
        if not match:
            raise LoreOutputError(f"Unrecognised lore revision diff line: {line!r}", text)
        path = match["path"].strip()
        changes.append(Change(match["code"], path.rstrip("/"), path.endswith("/")))
    return tuple(changes)


def parse_dependency_list(text: str) -> tuple[str, ...]:
    """Parse `lore file dependency list <one path> --tag houdini`.

    Output is a `<path as passed>:` header and then one `  <repo-relative dep>
    [<tags>]` line per edge. Returns the repo-relative dependency paths. A
    missing header, a second header or any other line raises.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or lines[0].startswith(" ") or not lines[0].endswith(":"):
        raise LoreOutputError("lore file dependency list did not start with '<path>:'", text)
    deps = []
    for line in lines[1:]:
        match = _DEPENDENCY.match(line)
        if not match:
            raise LoreOutputError(f"Unrecognised lore file dependency list line: {line!r}", text)
        deps.append(match["path"])
    return tuple(deps)


def parse_sync_blocked(text: str) -> tuple[str, ...]:
    """Return the repo-relative paths a refused `lore sync` names as locally changed.

    Reads `[Error] File has local changes: <path> (incoming size ...)` lines.
    Returns an empty tuple when there are none; the caller decides whether
    that is an error.
    """
    found = (_SYNC_BLOCKED.match(line.strip()) for line in text.splitlines())
    return tuple(dict.fromkeys(match["path"] for match in found if match))


def parse_version(text: str) -> tuple[int, int, int]:
    """Parse `lore --version`, e.g. `lore 0.9.0+783`."""
    match = _VERSION.match(text.strip())
    if not match:
        raise LoreOutputError("Unrecognised lore --version output", text)
    return int(match["major"]), int(match["minor"]), int(match["patch"])


def parse_status(text: str) -> Status:
    """Parse `lore status` output."""
    branch: str | None = None
    revision = 0
    remote_revision: int | None = None
    remote_note: str | None = None
    signature: str | None = None
    remote_signature: str | None = None
    unreachable = False
    section: str | None = None
    sections: dict[str, list[Change]] = {"staged": [], "unstaged": [], "untracked": []}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _REPOSITORY.match(stripped):
            continue
        if _CONNECTION_FAILED.match(stripped) or _REMOTE_UNREADABLE.match(stripped):
            unreachable = True
            continue
        if match := _ON_BRANCH.match(stripped):
            branch, revision, signature = match["branch"], int(match["rev"]), match["sig"]
            continue
        if match := _REMOTE_REVISION.match(stripped):
            remote_revision, remote_signature = int(match["rev"]), match["sig"]
            continue
        if stripped.startswith("Local branch "):
            remote_note = stripped
            continue
        if stripped in _SECTIONS:
            section = _SECTIONS[stripped]
            continue
        if stripped == "Tracked changes:" and section is not None:
            continue
        if _TRACKED_COUNT.match(stripped):
            continue
        if stripped == NO_CHANGES_LINE:
            continue
        match = _CHANGE.match(stripped)
        if match and section is not None:
            path = match["path"].strip()
            is_dir = path.endswith("/")
            sections[section].append(Change(match["code"], path.rstrip("/"), is_dir))
            continue
        raise LoreOutputError(f"Unrecognised lore status line: {line!r}", text)
    if branch is None:
        raise LoreOutputError("lore status printed no 'On branch' line", text)
    return Status(
        branch=branch,
        revision=revision,
        remote_revision=remote_revision,
        remote_note=remote_note,
        staged=tuple(sections["staged"]),
        unstaged=tuple(sections["unstaged"]),
        untracked=tuple(sections["untracked"]),
        signature=signature,
        remote_signature=remote_signature,
        unreachable=unreachable,
    )


def parse_auth_info(text: str) -> AuthInfo:
    """Parse `lore auth info` output: `ID:` and `Name:` lines."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key, sep, value = stripped.partition(":")
        if not sep or key not in ("ID", "Name"):
            raise LoreOutputError(f"Unrecognised lore auth info line: {line!r}", text)
        fields[key] = value.strip()
    if "ID" not in fields or "Name" not in fields:
        raise LoreOutputError("lore auth info is missing ID or Name", text)
    return AuthInfo(user_id=fields["ID"], name=fields["Name"])


def parse_branch_info(text: str) -> BranchInfo:
    """Parse `lore branch info` output for the current branch.

    The first line is `Branch <name>`; the rest are indented `Key: value`
    lines. Keys other than ID, Latest, Remote Latest, Creator and Created
    raise, and so does a missing ID.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    match = _BRANCH.match(lines[0]) if lines else None
    if not match:
        raise LoreOutputError("lore branch info did not start with 'Branch <name>'", text)
    fields: dict[str, str] = {}
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        if not sep or key not in BRANCH_INFO_KEYS or key in fields:
            raise LoreOutputError(f"Unrecognised lore branch info line: {line!r}", text)
        fields[key] = value.strip()
    if not fields.get("ID"):
        raise LoreOutputError("lore branch info is missing ID", text)
    return BranchInfo(name=match["name"].strip(), id=fields["ID"])


def parse_lock_query(text: str) -> tuple[LockRecord, ...]:
    """Parse `lore lock query` output.

    Each record reads `<path> by <owner> on branch <branch id>`. The owner is
    the `Name` from `lore auth info`, and the branch is an ID, not a name.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "Locks found:":
        raise LoreOutputError("lore lock query did not start with 'Locks found:'", text)
    records = []
    for line in lines[1:]:
        match = _LOCK.match(line)
        if not match:
            raise LoreOutputError(f"Unrecognised lore lock query line: {line!r}", text)
        records.append(LockRecord(match["path"].strip(), match["owner"].strip(), match["branch"]))
    return tuple(records)


def parse_lock_acquire(text: str) -> frozenset[str]:
    """Parse `lore lock acquire` output into the paths the caller now holds.

    Paths come under `Lock acquired on files:` or `Lock already owned on
    files:`. Any other header raises, so a refusal never reads as success.
    """
    held: set[str] = set()
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in LOCK_HELD_HEADERS:
            in_section = True
            continue
        if stripped.endswith(":") or not in_section:
            raise LoreOutputError(f"Unrecognised lore lock acquire line: {line!r}", text)
        held.add(stripped)
    return frozenset(held)


def parse_file_info(text: str) -> dict[str, FileInfo]:
    """Parse `lore file info` output into blocks keyed by repo-relative path.

    Lore prints one block per path that exists on disk, in no fixed order, and
    nothing for a path that does not exist. Unknown keys or Status values raise.
    Files with dependency links add `dependencies: <N bytes>` and `dependents:
    <N bytes>` lines, which a batch prints after the last block, so they are
    skipped.
    """
    infos: dict[str, FileInfo] = {}
    block: dict[str, str] = {}

    def close() -> None:
        if not block:
            return
        missing = [key for key in FILE_INFO_KEYS if key not in block]
        if missing:
            raise LoreOutputError(f"lore file info block for {block['Path']!r} lacks {missing}", text)
        if block["Status"] not in FILE_STATUS_VALUES:
            raise LoreOutputError(f"Unknown lore file info Status: {block['Status']!r}", text)
        if not block["Size"].isdigit():
            raise LoreOutputError(f"Unrecognised lore file info Size: {block['Size']!r}", text)
        infos[block["Path"]] = FileInfo(path=block["Path"], status=block["Status"])
        block.clear()

    for line in text.splitlines():
        if not line.strip() or _LINK_SIZE.match(line.strip()):
            continue
        key, sep, value = line.strip().partition(":")
        if not sep or key not in FILE_INFO_KEYS:
            raise LoreOutputError(f"Unrecognised lore file info line: {line!r}", text)
        if key == "Path":
            close()
        elif not block or key in block:
            raise LoreOutputError(f"Unexpected lore file info line: {line!r}", text)
        block[key] = value.strip()
    close()
    return infos
