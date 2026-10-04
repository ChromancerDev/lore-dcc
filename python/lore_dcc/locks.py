"""Lock states and the read-only rule that enforces them on disk.

Lore locks are advisory: the server records them but does not block a commit.
The plugin enforces them with the read-only bit, the same rule on every DCC.
"""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

CASE_INSENSITIVE = sys.platform == "win32"


def path_key(path: str) -> str:
    """Return the form of a repo-relative path to compare on this platform.

    Windows paths compare case-insensitively. Keep Lore's own spelling for
    display and results; use the key only for matching.
    """
    return path.casefold() if CASE_INSENSITIVE else path


class LockState(Enum):
    """Lock state of one file, as far as the plugin can tell."""

    UNKNOWN = "unknown"
    NOT_LOCKED = "not_locked"
    LOCKED_BY_ME = "locked_by_me"
    LOCKED_BY_OTHER = "locked_by_other"


@dataclass(frozen=True)
class LockRecord:
    """One lock reported by `lore lock query`.

    Attributes:
        path: Repo-relative path with forward slashes.
        owner: The owner as Lore prints it, which is the user's Name.
        branch: The ID of the branch the lock is held on, if printed.
    """

    path: str
    owner: str
    branch: str | None


@dataclass(frozen=True)
class Identity:
    """Every name Lore may print for the current user.

    Built from the effective identity in `.lore/config.toml` plus the ID and
    Name lines of `lore auth info`.
    """

    names: frozenset[str]

    @classmethod
    def of(cls, *names: str | None) -> Identity:
        """Build an identity from any number of names, skipping blanks."""
        return cls(frozenset(n.strip() for n in names if n and n.strip()))

    def owns(self, owner: str) -> bool:
        """Return True when `owner` is one of this identity's names."""
        return owner.strip() in self.names


def find_lock(
    path: str, locks: Iterable[LockRecord], branch_id: str | None = None
) -> LockRecord | None:
    """Return the first lock record for a path on the current branch, or None.

    Args:
        path: Repo-relative path with forward slashes.
        locks: Records from a successful lock query.
        branch_id: The current branch ID. When given, records whose branch is
            set and differs are ignored.
    """
    key = path_key(path)
    for lock in locks:
        if path_key(lock.path) != key:
            continue
        if branch_id is not None and lock.branch is not None and lock.branch != branch_id:
            continue
        return lock
    return None


def classify(
    path: str,
    locks: Iterable[LockRecord] | None,
    me: Identity,
    branch_id: str | None = None,
) -> LockState:
    """Return the lock state of a repo-relative path.

    Args:
        path: Repo-relative path with forward slashes.
        locks: Every lock from a successful query, or None if the query failed.
        me: The current user's identity.
        branch_id: The current branch ID; locks held on other branches are
            ignored when given.
    """
    if locks is None:
        return LockState.UNKNOWN
    lock = find_lock(path, locks, branch_id)
    if lock is None:
        return LockState.NOT_LOCKED
    if not lock.owner.strip() or lock.owner.strip() == "<unknown>":
        return LockState.UNKNOWN
    return LockState.LOCKED_BY_ME if me.owns(lock.owner) else LockState.LOCKED_BY_OTHER


def wanted_read_only(state: LockState, *, tracked: bool, lockable: bool) -> bool | None:
    """Return the read-only bit a file should have, or None to leave it alone.

    Args:
        state: The file's lock state.
        tracked: True when the file exists in the current revision.
        lockable: True when the file type is lockable.
    """
    if not lockable or state is LockState.UNKNOWN:
        return None
    if state is LockState.LOCKED_BY_OTHER:
        return True
    return tracked and state is not LockState.LOCKED_BY_ME


def set_read_only(path: Path, read_only: bool) -> bool:
    """Set or clear the owner-write bit on a file.

    Returns:
        True when the bit changed.
    """
    mode = os.stat(path).st_mode
    new_mode = mode & ~stat.S_IWRITE if read_only else mode | stat.S_IWRITE
    if new_mode == mode:
        return False
    os.chmod(path, new_mode)
    return True
