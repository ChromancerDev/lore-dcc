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
_LOCK = re.compile(r"^(?P<path>.+) by (?P<owner>.+?) on branch (?P<branch>\S+)$")



class RemoteState(Enum):
    """How the local branch relates to the remote, from the `Local branch` line."""

    IN_SYNC = "in_sync"
    BEHIND = "behind"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"


_REMOTE_NOTES = {
    "Local branch in sync with remote": RemoteState.IN_SYNC,
    "Local branch is behind remote": RemoteState.BEHIND,
    "Local branch has diverged, synchronize to merge": RemoteState.DIVERGED,
}

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
    """

    branch: str
    revision: int
    remote_revision: int | None
    remote_note: str | None
    staged: tuple[Change, ...]
    unstaged: tuple[Change, ...]
    untracked: tuple[Change, ...]

    @property
    def remote_state(self) -> RemoteState:
        """The remote state, UNKNOWN when the line is missing or unfamiliar."""
        return _REMOTE_NOTES.get(self.remote_note or "", RemoteState.UNKNOWN)


@dataclass(frozen=True)
class AuthInfo:
    """Parsed `lore auth info` output."""

    user_id: str
    name: str


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
    section: str | None = None
    sections: dict[str, list[Change]] = {"staged": [], "unstaged": [], "untracked": []}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _REPOSITORY.match(stripped):
            continue
        if match := _ON_BRANCH.match(stripped):
            branch, revision = match["branch"], int(match["rev"])
            continue
        if match := _REMOTE_REVISION.match(stripped):
            remote_revision = int(match["rev"])
            continue
        if stripped.startswith("Local branch "):
            remote_note = stripped
            continue
        if stripped in _SECTIONS:
            section = _SECTIONS[stripped]
            continue
        if stripped == "Tracked changes:" and section is not None:
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
