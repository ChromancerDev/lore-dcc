"""Lore operations for one working copy, shared by every DCC.

Stdlib only. Nothing here imports a DCC or Qt, so every method can run on a
worker thread. Paths in arguments may be absolute or repo-relative; paths in
results are repo-relative with forward slashes.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

from lore_dcc.backend import parsers
from lore_dcc.backend.cli import LoreCli
from lore_dcc.backend.parsers import FileInfo, Status
from lore_dcc.errors import LockNotAcquired, LoreCommandError, LoreError
from lore_dcc.locks import (
    Identity,
    LockRecord,
    LockState,
    classify,
    find_lock,
    path_key,
    set_read_only,
    wanted_read_only,
)
from lore_dcc.settings import Settings, load_settings
from lore_dcc.workspace import Workspace, find_workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileState:
    """What the plugin knows about one file.

    Attributes:
        path: Repo-relative path.
        exists: True when the file is on disk.
        tracked: True when the current revision has the file.
        lockable: True when the file type is lockable per Settings.
        lock: Lock state; UNKNOWN when the lock query failed.
        owner: Lock owner as Lore prints it, if locked.
    """

    path: str
    exists: bool
    tracked: bool
    lockable: bool
    lock: LockState
    owner: str | None

    @property
    def blocked(self) -> bool:
        """True when this file cannot be committed: someone else's lock, or unknown."""
        return self.lockable and self.lock in (LockState.LOCKED_BY_OTHER, LockState.UNKNOWN)


@dataclass(frozen=True)
class WriteResult:
    """Outcome of finish_write.

    Attributes:
        staged: New files that were staged.
        changed: Tracked files whose bytes changed; their locks are kept.
        released: Tracked files written byte-identical; their locks were released.
    """

    staged: tuple[str, ...]
    changed: tuple[str, ...]
    released: tuple[str, ...]


@dataclass(frozen=True)
class CommitResult:
    """Outcome of commit or retry_push.

    Attributes:
        committed: True when a local revision was made.
        pushed: True when the revision reached the server.
        diverged: True when push failed because the branch is behind remote.
        push_error: Lore's push output when push failed, else None.
        output: Lore's commit and push output, for display.
        revision: The revision this commit made, when Lore printed it.
        pushed_revision: The newest revision the push sent, when Lore printed it.
        branch: The branch the push went to, when Lore printed it.
        server_revision: The remote revision after the call, when it could be read.
    """

    committed: bool
    pushed: bool
    diverged: bool
    push_error: str | None
    output: str
    revision: int | None = None
    pushed_revision: int | None = None
    branch: str | None = None
    server_revision: int | None = None


@dataclass(frozen=True)
class _Pending:
    """A local commit that has not reached the server yet."""

    paths: tuple[str, ...]
    keep_locked: bool


def new_file_message(path: str) -> str:
    """Return the refusal text for discarding a file Lore does not track."""
    return f"{path} is new. Lore has no committed version to go back to."


class CommitBlocked(Exception):
    """commit() was asked to include files someone else locked or whose lock is unknown."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__(f"Cannot commit {len(paths)} file(s): {', '.join(paths[:5])}")
        self.paths = paths


class Session:
    """Lore state and operations for one working copy.

    Attributes:
        workspace: The working copy.
        lore: The CLI backend.
        settings: Per-repo settings.
        touched: Repo-relative paths this session locked, staged or saved.
            The Commit dialog checks them by default.
        outputs: Repo-relative paths the output flow wrote and Lore reports changed.

    The sets are mutated under an internal lock; callers that iterate them
    while a worker thread runs should copy them first.
    """

    workspace: Workspace
    lore: LoreCli
    settings: Settings
    touched: set[str]
    outputs: set[str]

    def __init__(self, workspace: Workspace, lore: LoreCli, settings: Settings) -> None:
        self.workspace = workspace
        self.lore = lore
        self.settings = settings
        self.touched = set()
        self.outputs = set()
        self._identity: Identity | None = None
        self._branch_id: str | None = None
        self._pending: list[_Pending] = []
        self._taken: set[str] = set()
        self._lock = threading.RLock()

    def identity(self) -> Identity:
        """Return the current user's identity; `lore auth info` runs once per session.

        Raises:
            LoreCommandError: `lore auth info` failed, usually because the user
                is not logged in. Failures are not cached.
        """
        with self._lock:
            if self._identity is None:
                info = self.lore.auth_info()
                self._identity = Identity.of(self.workspace.identity, info.user_id, info.name)
            return self._identity

    def branch_id(self) -> str | None:
        """Return the current branch ID; `lore branch info` runs once per session.

        Returns None when the lookup fails, which turns branch filtering off so
        every lock counts. Failures are not cached.
        """
        with self._lock:
            if self._branch_id is None:
                try:
                    self._branch_id = self.lore.branch_info().id
                except LoreError as exc:
                    logger.warning("Branch lookup failed, locks on every branch count: %s", exc)
            return self._branch_id

    def invalidate(self) -> None:
        """Forget the cached identity and branch, e.g. after a file load."""
        with self._lock:
            self._identity = None
            self._branch_id = None

    def file_states(self, paths: Sequence[Path | str]) -> list[FileState]:
        """Return one FileState per path, in input order.

        Runs one `lore file info` and one `lore lock query`. A failed lock query
        gives LockState.UNKNOWN for every path instead of raising. A path that
        is absent from `lore file info` counts as not tracked, so a tracked file
        deleted from disk is reported as tracked only when Lore prints a
        `Deleted` block for it. Locks held on other branches are ignored.
        """
        rels = [self._rel(p) for p in paths]
        if not rels:
            return []
        me = self.identity()
        infos = self._file_infos(rels)
        locks = self._query_locks()
        branch_id = self.branch_id() if locks is not None else None
        return [self._state(rel, infos.get(path_key(rel)), locks, me, branch_id) for rel in rels]

    def is_staged(self, path: Path | str) -> bool:
        """Return True when `lore status` lists the file under staged changes."""
        key = path_key(self._rel(path))
        return any(not c.is_dir and path_key(c.path) == key for c in self.lore.status().staged)

    def lock(self, paths: Sequence[Path | str]) -> list[FileState]:
        """Lock files, stage the untracked ones, clear their read-only bit.

        Returns the states after the operation.

        Raises:
            LockNotAcquired: Lore did not report holding every path.
        """
        rels = self._unique(paths)
        if not rels:
            return []
        infos = self._file_infos(rels)
        self.lore.lock_acquire([self._abs(r) for r in rels])
        untracked = [
            r
            for r in rels
            if self._abs(r).exists()
            and not ((info := infos.get(path_key(r))) is not None and info.tracked)
        ]
        if untracked:
            self.lore.stage([self._abs(r) for r in untracked])
        self._set_read_only(rels, False)
        self._touch(rels)
        return self.file_states(rels)

    def release(self, paths: Sequence[Path | str]) -> None:
        """Release the current user's locks, then reapply the read-only rule."""
        rels = self._unique(paths)
        if not rels:
            return
        self.lore.lock_release([self._abs(r) for r in rels])
        self._forget_taken(rels)
        self.refresh_read_only(rels)

    def discard(self, paths: Sequence[Path | str]) -> None:
        """Reset files to the current revision, release own locks, reapply read-only.

        Raises:
            ValueError: A path is not tracked, so Lore has no committed version
                to go back to. Raised before Lore runs.
        """
        rels = self._unique(paths)
        if not rels:
            return
        states = self.file_states(rels)
        new = [s.path for s in states if not s.tracked]
        if new:
            raise ValueError("\n".join(new_file_message(p) for p in new))
        mine = [s.path for s in states if s.lock is LockState.LOCKED_BY_ME]
        self.lore.discard([self._abs(r) for r in rels])
        if mine:
            self.lore.lock_release([self._abs(r) for r in mine])
        self._forget_taken(rels)
        self._drop(self.touched, rels)
        self._drop(self.outputs, rels)
        self.refresh_read_only(rels)

    def make_writable(self, paths: Sequence[Path | str]) -> None:
        """Clear the read-only bit without a lock, for 'Make writable'."""
        self._set_read_only(self._unique(paths), False)

    def stage(self, paths: Sequence[Path | str]) -> None:
        """Stage files and add them to touched."""
        rels = self._unique(paths)
        if not rels:
            return
        self.lore.stage([self._abs(r) for r in rels])
        self._touch(rels)

    def mark_saved(self, paths: Sequence[Path | str]) -> None:
        """Run `lore dirty` on files the DCC just saved and add them to touched.

        Paths that are not on disk are skipped by `lore dirty` but still added
        to touched.
        """
        rels = self._unique(paths)
        existing = [r for r in rels if self._abs(r).exists()]
        if existing:
            self.lore.dirty([self._abs(r) for r in existing])
        self._touch(rels)

    def refresh_read_only(self, paths: Sequence[Path | str]) -> None:
        """Apply locks.wanted_read_only to each existing file."""
        rels = self._unique(paths)
        if rels:
            self._apply_read_only(self.file_states(rels))

    def prepare_write(self, paths: Sequence[Path | str]) -> tuple[str, ...]:
        """Before a DCC writes outputs: lock the tracked lockable ones not already mine.

        New files need nothing. Returns the paths locked by this call, and
        clears their read-only bit so the DCC can write.

        Raises:
            LockNotAcquired: Someone else holds a lock on a tracked lockable
                path. Raised before any lock is taken; `paths` lists the files
                and `output` names the owners. Files whose lock state is
                UNKNOWN are attempted anyway, since Lore refuses a lock held by
                someone else.
        """
        rels = self._unique(paths)
        if not rels:
            return ()
        states = [s for s in self.file_states(rels) if s.tracked and s.lockable]
        others = [s for s in states if s.lock is LockState.LOCKED_BY_OTHER]
        if others:
            raise LockNotAcquired(
                [s.path for s in others], "\n".join(f"{s.path} locked by {s.owner}" for s in others)
            )
        targets = [s.path for s in states if s.lock is not LockState.LOCKED_BY_ME]
        if not targets:
            return ()
        self.lore.lock_acquire([self._abs(r) for r in targets])
        with self._lock:
            self._taken.update(path_key(r) for r in targets)
        self._set_read_only(targets, False)
        self._touch(targets)
        return tuple(targets)

    def finish_write(self, paths: Sequence[Path | str]) -> WriteResult:
        """After a DCC wrote outputs: dirty, check-dirty, stage new, release unchanged.

        Only paths that exist on disk are considered. A tracked file that
        `lore status --check-dirty` no longer reports is byte-identical to its
        revision; its lock is released only when prepare_write took it in this
        session. Locks from `lock()` or held before stay.
        """
        existing = [r for r in self._unique(paths) if self._abs(r).exists()]
        if not existing:
            return WriteResult((), (), ())
        absolute = [self._abs(r) for r in existing]
        self.lore.dirty(absolute)
        reported = _reported_keys(self.lore.check_dirty(absolute))
        states = self.file_states(existing)
        with self._lock:
            taken = set(self._taken)
        new = [s.path for s in states if not s.tracked]
        changed = [s.path for s in states if s.tracked and path_key(s.path) in reported]
        unchanged = [s for s in states if s.tracked and path_key(s.path) not in reported]
        released = [
            s for s in unchanged if s.lock is LockState.LOCKED_BY_ME and path_key(s.path) in taken
        ]
        if new:
            self.lore.stage([self._abs(r) for r in new])
        if released:
            self.lore.lock_release([self._abs(s.path) for s in released])
            self._forget_taken(s.path for s in released)
            for state in released:
                wanted = wanted_read_only(
                    LockState.NOT_LOCKED, tracked=True, lockable=state.lockable
                )
                if wanted is not None:
                    self._set_read_only([state.path], wanted)
        with self._lock:
            self.touched.update(new, changed)
            self.outputs.update(new, changed)
        self._drop(self.outputs, [s.path for s in unchanged])
        return WriteResult(tuple(new), tuple(changed), tuple(s.path for s in released))

    def status(self) -> Status:
        """Return `lore status` for the whole working copy."""
        return self.lore.status()

    def commit(
        self,
        include: Sequence[str],
        summary: str,
        description: str = "",
        push: bool = True,
        keep_locked: bool = False,
    ) -> CommitResult:
        """Stage `include`, unstage anything else staged, commit, optionally push.

        After a successful push, own locks on the files of commits made without
        keep_locked are released, except files `lore status --check-dirty`
        reports changed again, and the read-only rule is reapplied. A failed push
        does not raise: the commit is safe locally and the result carries
        `pushed=False`, `push_error` and `diverged`. A failed commit raises.

        Committed paths leave `touched` and `outputs` as soon as the local
        commit succeeds, and are remembered for retry_push. Staged directory
        entries are left alone when unstaging, so a directory staged for a file
        outside `include` stays staged.

        Raises:
            CommitBlocked: `include` holds a blocked file.
            ValueError: `summary` is blank or `include` is empty.
            LoreCommandError: Staging or the commit failed.
        """
        summary = summary.strip()
        if not summary:
            raise ValueError("A commit summary is required.")
        rels = self._unique(include)
        if not rels:
            raise ValueError("Nothing is included in the commit.")
        blocked = [s.path for s in self.file_states(rels) if s.blocked]
        if blocked:
            raise CommitBlocked(blocked)
        self.lore.stage([self._abs(r) for r in rels])
        wanted = {path_key(r) for r in rels}
        wanted_dirs = {path_key(parent.as_posix()) for r in rels for parent in Path(r).parents}
        # Unstaging a folder is recursive, so only folders holding nothing included are unstaged.
        extra = [
            c.path
            for c in self.lore.status().staged
            if path_key(c.path) not in (wanted_dirs if c.is_dir else wanted)
        ]
        if extra:
            self.lore.unstage([self._abs(r) for r in extra])
        message = summary
        if description.strip():
            message = f"{summary}\n\n{description.strip()}"
        output = self.lore.commit(message)
        with self._lock:
            self._pending.append(_Pending(tuple(rels), keep_locked))
        self._drop(self.touched, rels)
        self._drop(self.outputs, rels)
        self._forget_taken(rels)
        revision = parsers.committed_revision(output)
        if not push:
            return CommitResult(
                True,
                False,
                False,
                None,
                output,
                revision=revision,
                server_revision=self._server_revision(),
            )
        return self._push(output, revision)

    def retry_push(self) -> CommitResult:
        """Push existing local commits again after a failed push.

        Each earlier commit keeps its own `keep_locked` choice. With no commit
        remembered in this session it only pushes. `committed` is True because
        the local commits already exist.
        """
        return self._push("", None)

    def _server_revision(self) -> int | None:
        try:
            return self.lore.revision_status().remote_revision
        except LoreError as exc:
            logger.warning("Could not read the server revision: %s", exc)
            return None

    def _push(self, output: str, revision: int | None) -> CommitResult:
        with self._lock:
            pending = list(self._pending)
        try:
            pushed_output = self.lore.push()
        except LoreCommandError as exc:
            error = exc.output.strip() or str(exc)
            logger.warning("Push failed: %s", error)
            return CommitResult(
                True,
                False,
                "Branch has diverged" in exc.output,
                error,
                _join(output, error),
                revision=revision,
            )
        with self._lock:
            self._pending.clear()
        output = _join(output, pushed_output)
        try:
            self._after_push(pending)
        except LoreError as exc:
            logger.warning("Lock cleanup after push failed: %s", exc)
            output = _join(output, f"Pushed, but lock cleanup failed: {exc}")
        pushed = parsers.pushed_revision(pushed_output)
        return CommitResult(
            True,
            True,
            False,
            None,
            output,
            revision=revision,
            pushed_revision=pushed.revision if pushed else None,
            branch=pushed.branch if pushed else None,
            server_revision=self._server_revision(),
        )

    def _after_push(self, pending: list[_Pending]) -> None:
        everything = list(dict.fromkeys(p for commit in pending for p in commit.paths))
        if not everything:
            return
        kept = {path_key(p) for commit in pending if commit.keep_locked for p in commit.paths}
        releasable = list(
            dict.fromkeys(
                p
                for commit in pending
                if not commit.keep_locked
                for p in commit.paths
                if path_key(p) not in kept
            )
        )
        if releasable:
            edited = _reported_keys(self.lore.check_dirty([self._abs(r) for r in releasable]))
            releasable = [p for p in releasable if path_key(p) not in edited]
        if releasable:
            mine = [
                s.path for s in self.file_states(releasable) if s.lock is LockState.LOCKED_BY_ME
            ]
            if mine:
                self.lore.lock_release([self._abs(r) for r in mine])
        self.refresh_read_only(everything)

    def _rel(self, path: Path | str) -> str:
        path = Path(path)
        if path.is_absolute():
            return self.workspace.relative(path)
        return path.as_posix()

    def _abs(self, rel: str) -> Path:
        return self.workspace.root / rel

    def _unique(self, paths: Iterable[Path | str]) -> list[str]:
        return list(dict.fromkeys(self._rel(p) for p in paths))

    def _touch(self, rels: Iterable[str]) -> None:
        with self._lock:
            self.touched.update(rels)

    def _drop(self, target: set[str], rels: Iterable[str]) -> None:
        keys = {path_key(r) for r in rels}
        with self._lock:
            for item in [p for p in target if path_key(p) in keys]:
                target.discard(item)

    def _forget_taken(self, rels: Iterable[str]) -> None:
        with self._lock:
            self._taken.difference_update(path_key(r) for r in rels)

    def _file_infos(self, rels: Sequence[str]) -> dict[str, FileInfo]:
        infos = self.lore.file_info([self._abs(r) for r in dict.fromkeys(rels)])
        return {path_key(path): info for path, info in infos.items()}

    def _query_locks(self) -> tuple[LockRecord, ...] | None:
        try:
            return self.lore.lock_query()
        except LoreError as exc:
            logger.warning("Lock query failed, lock state is unknown: %s", exc)
            return None

    def _state(
        self,
        rel: str,
        info: FileInfo | None,
        locks: tuple[LockRecord, ...] | None,
        me: Identity,
        branch_id: str | None,
    ) -> FileState:
        record = find_lock(rel, locks or (), branch_id)
        owner = record.owner if record else None
        return FileState(
            path=rel,
            exists=self._abs(rel).exists(),
            tracked=info is not None and info.tracked,
            lockable=self.settings.is_lockable(rel),
            lock=classify(rel, locks, me, branch_id),
            owner=owner,
        )

    def _apply_read_only(self, states: Iterable[FileState]) -> None:
        for state in states:
            wanted = wanted_read_only(state.lock, tracked=state.tracked, lockable=state.lockable)
            if wanted is not None and state.exists:
                self._set_read_only([state.path], wanted)

    def _set_read_only(self, rels: Iterable[str], read_only: bool) -> None:
        for rel in rels:
            target = self._abs(rel)
            if not target.is_file():
                continue
            try:
                set_read_only(target, read_only)
            except OSError as exc:
                logger.warning("Could not change the read-only bit on %s: %s", target, exc)


def _reported_keys(status: Status) -> set[str]:
    changes = chain(status.staged, status.unstaged, status.untracked)
    return {path_key(c.path) for c in changes}


def _join(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if part.strip())


_SESSIONS: dict[Path, Session] = {}
_SESSIONS_LOCK = threading.Lock()


def session_for(path: Path | str) -> Session:
    """Return the cached Session for the working copy holding `path`.

    The first call for a working copy runs `lore --version` and reads
    `lore-dcc.json`; later calls reuse the Session. Failures are not cached.

    Raises:
        NotAWorkingCopy: `path` is not inside a Lore working copy.
        LoreNotFound: `lore` is not on PATH.
        LoreVersionError: Lore is older than the floor.
        SettingsError: `lore-dcc.json` is malformed.
    """
    workspace = find_workspace(Path(path))
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(workspace.root)
        if session is None:
            lore = LoreCli(workspace.root)
            lore.version()
            session = Session(workspace, lore, load_settings(workspace.root))
            _SESSIONS[workspace.root] = session
        return session
