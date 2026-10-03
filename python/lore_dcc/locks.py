"""Lock states and the read-only rule that enforces them on disk.

Lore locks are advisory: the server records them but does not block a commit.
The plugin enforces them with the read-only bit, the same rule on every DCC.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


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


def classify(path: str, locks: Iterable[LockRecord] | None, me: Identity) -> LockState:
    """Return the lock state of a repo-relative path.

    Args:
        path: Repo-relative path with forward slashes.
        locks: Every lock from a successful query, or None if the query failed.
        me: The current user's identity.
    """
    if locks is None:
        return LockState.UNKNOWN
    for lock in locks:
        if lock.path != path:
            continue
        if not lock.owner.strip() or lock.owner.strip() == "<unknown>":
            return LockState.UNKNOWN
        return LockState.LOCKED_BY_ME if me.owns(lock.owner) else LockState.LOCKED_BY_OTHER
    return LockState.NOT_LOCKED


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
