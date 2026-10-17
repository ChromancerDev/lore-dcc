"""Run the `lore` executable as a subprocess.

Every call passes absolute paths, because Lore resolves relative paths against
the process working folder, which inside a DCC is arbitrary.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path

from lore_dcc.backend import parsers
from lore_dcc.errors import (
    LockNotAcquired,
    LoreCommandError,
    LoreNotFound,
    LoreVersionError,
    ServerUnreachable,
)
from lore_dcc.locks import LockRecord, path_key

logger = logging.getLogger(__name__)

MIN_VERSION = (0, 9, 0)
TESTED_VERSION = (0, 9, 0)
BATCH_SIZE = 500
BATCH_CHARS = 24_000
DEFAULT_TIMEOUT = 120.0
SYNC_TIMEOUT = 3600.0
ROOTS_ONLY_TAG = "lore-dcc-roots-only"
DISCONNECTED = "Disconnected from server"
HOST_CA_VARIABLES = ("SSL_CERT_FILE", "SSL_CERT_DIR")

Runner = Callable[..., subprocess.CompletedProcess]


class LoreCli:
    """One working copy's view of the Lore CLI.

    Args:
        root: The working copy root. Commands run with it as their working folder.
        executable: Path to `lore`. Found on PATH when omitted.
        timeout: Seconds before a command is abandoned.
        runner: Replaces `subprocess.run` in tests.

    Attributes:
        link_tag: The tag on the dependency links this DCC reads and writes.
    """

    link_tag: str = "houdini"

    def __init__(
        self,
        root: Path,
        executable: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        runner: Runner = subprocess.run,
    ) -> None:
        self.root = Path(root).resolve()
        self.timeout = timeout
        self._runner = runner
        found = executable or shutil.which("lore")
        if not found:
            raise LoreNotFound("The lore executable is not on PATH.")
        self.executable = found

    def run(self, *args: str, paths: Sequence[Path] = ()) -> str:
        """Run one command, batching paths, and return the joined stdout.

        Raises:
            LoreCommandError: A batch exited non-zero or timed out.
        """
        if not paths:
            return self._run_once(list(args))
        outputs = [self._run_once([*args, *batch]) for batch in self._batches(paths)]
        return "\n".join(outputs)

    def version(self) -> tuple[int, int, int]:
        """Return the Lore version, refusing anything below the floor.

        Raises:
            LoreVersionError: Lore is older than MIN_VERSION.
        """
        version = parsers.parse_version(self._run_once(["--version"], global_flags=False))
        if version < MIN_VERSION:
            raise LoreVersionError(
                f"Lore {_dotted(version)} is too old. This plugin needs {_dotted(MIN_VERSION)}."
            )
        if version > TESTED_VERSION:
            logger.warning("Lore %s is newer than the tested %s.", _dotted(version), _dotted(TESTED_VERSION))
        return version

    def status(self) -> parsers.Status:
        """Return `lore status` for the whole working copy."""
        return self._status(["status"], ())

    def revision_status(self) -> parsers.Status:
        """Return `lore status --revision-only`: local and remote revision, no diffs."""
        return self._status(["status", "--revision-only"], ())

    def check_dirty(self, paths: Sequence[Path]) -> parsers.Status:
        """Re-verify dirty files against disk; unchanged ones drop out."""
        return self._status(["status", "--check-dirty"], paths)

    def dirty(self, paths: Sequence[Path]) -> None:
        """Mark files dirty without reading their content."""
        self.run("dirty", paths=paths)

    def stage(self, paths: Sequence[Path]) -> None:
        """Stage files for the next commit."""
        self.run("stage", paths=paths)

    def unstage(self, paths: Sequence[Path]) -> None:
        """Remove files from the next commit."""
        self.run("unstage", paths=paths)

    def commit(self, message: str) -> str:
        """Commit everything staged. Returns Lore's output for display."""
        return self._run_once(["commit", message])

    def push(self) -> str:
        """Push local commits. Returns Lore's output for display."""
        return self._run_once(["push"])

    def lock_acquire(self, paths: Sequence[Path]) -> None:
        """Acquire locks on files, new or tracked.

        Raises:
            LockNotAcquired: Lore did not report holding every path.
        """
        held: set[str] = set()
        outputs = []
        for batch in self._batches(paths):
            output = self._run_once(["lock", "acquire", *batch])
            outputs.append(output)
            held |= parsers.parse_lock_acquire(output)
        wanted = [Path(p).relative_to(self.root).as_posix() for p in self._absolute(paths)]
        held_keys = {path_key(p) for p in held}
        missing = [p for p in wanted if path_key(p) not in held_keys]
        if missing:
            raise LockNotAcquired(missing, "\n".join(outputs))

    def lock_release(self, paths: Sequence[Path]) -> None:
        """Release the current user's locks on files."""
        self.run("lock", "release", paths=paths)

    def lock_query(self) -> tuple[LockRecord, ...]:
        """Return every lock on the server for this repository."""
        return parsers.parse_lock_query(self._run_once(["lock", "query"]))

    def discard(self, paths: Sequence[Path]) -> None:
        """Reset files to the current revision, dropping local changes."""
        self.run("file", "reset", paths=paths)

    def auth_info(self) -> parsers.AuthInfo:
        """Return the signed-in user."""
        return parsers.parse_auth_info(self._run_once(["auth", "info"]))

    def branch_info(self) -> parsers.BranchInfo:
        """Return the current branch, including its ID."""
        return parsers.parse_branch_info(self._run_once(["branch", "info"]))

    def file_info(self, paths: Sequence[Path]) -> dict[str, parsers.FileInfo]:
        """Return `lore file info` blocks keyed by repo-relative path.

        A path that does not exist on disk has no entry. Returns an empty dict
        without running Lore when `paths` is empty.
        """
        infos: dict[str, parsers.FileInfo] = {}
        for batch in self._batches(paths):
            infos.update(parsers.parse_file_info(self._run_once(["file", "info", *batch])))
        return infos

    def dependency_list(self, source: Path) -> tuple[str, ...]:
        """Return the repo-relative paths `source` links to with `link_tag`.

        Raises:
            LoreCommandError: Lore failed, e.g. `source` is neither tracked nor staged.
        """
        return parsers.parse_dependency_list(
            self._run_once(["file", "dependency", "list", f"--tag={self.link_tag}", self._one(source)])
        )

    def dependency_add(self, source: Path, deps: Sequence[Path]) -> None:
        """Link `source` to `deps` with `link_tag`. All or nothing in Lore.

        Both ends must be tracked or staged. Does nothing when `deps` is empty.
        """
        if deps:
            self.run(
                "file", "dependency", "add", f"--tag={self.link_tag}", self._one(source), paths=deps
            )

    def dependency_remove(self, source: Path, deps: Sequence[Path]) -> None:
        """Drop `link_tag` from links `source` -> `deps`. Missing links are fine.

        Does nothing when `deps` is empty.
        """
        if deps:
            tag = f"--tag={self.link_tag}"
            self.run("file", "dependency", "remove", tag, self._one(source), paths=deps)

    def revision_diff(self, source: str, target: str) -> tuple[parsers.Change, ...]:
        """Return the paths that changed between two revision signatures.

        Signatures only: Lore answers an unsynced revision number with a zero
        block and exit 0.
        """
        return parsers.parse_revision_diff(
            self._run_once(["revision", "diff", source, "--target", target])
        )

    def sync_roots(self, roots: Sequence[str], dry_run: bool = False) -> str:
        """Run one scoped `lore sync`, each repo-relative path in `roots` its own `--root-file`.

        `--root-file` is the one argument Lore needs repo-relative: an
        absolute path fails. It runs as a single call, because the first
        scoped sync moves the branch to the remote revision and a second call
        would find nothing to sync. A bare `--root-file` also pulls in the
        root's linked files, so `--dependency-tag` names a tag no link carries
        and the sync covers the roots alone.

        Raises:
            ValueError: `roots` is empty, or longer than one command line holds.
            LoreCommandError: The sync failed; exit 39 means local changes
                blocked it, see `parsers.parse_sync_blocked`.
        """
        if not roots:
            raise ValueError("No root files to sync.")
        args = [arg for root in roots for arg in ("--root-file", root)]
        if sum(len(arg) + 3 for arg in args) > BATCH_CHARS:
            raise ValueError(f"{len(roots)} root files are too many for one lore sync call.")
        dry = ["--dry-run"] if dry_run else []
        return self._run_once(
            ["sync", *args, "--dependency-tag", ROOTS_ONLY_TAG, *dry], timeout=SYNC_TIMEOUT
        )

    def _one(self, path: Path) -> str:
        return str((self.root / path).resolve())

    def _absolute(self, paths: Sequence[Path]) -> list[str]:
        return [str((self.root / p).resolve()) for p in paths]

    def _batches(self, paths: Sequence[Path]) -> Iterator[list[str]]:
        batch: list[str] = []
        length = 0
        for path in self._absolute(paths):
            if batch and (len(batch) >= BATCH_SIZE or length + len(path) + 3 > BATCH_CHARS):
                yield batch
                batch, length = [], 0
            batch.append(path)
            length += len(path) + 3
        if batch:
            yield batch

    def _status(self, args: list[str], paths: Sequence[Path]) -> parsers.Status:
        if not paths:
            return parsers.parse_status(self._run_once(args))
        results = [parsers.parse_status(self._run_once([*args, *b])) for b in self._batches(paths)]
        first = results[0]
        return replace(
            first,
            staged=tuple(c for r in results for c in r.staged),
            unstaged=tuple(c for r in results for c in r.unstaged),
            untracked=tuple(c for r in results for c in r.untracked),
        )

    def _run_once(
        self, args: list[str], global_flags: bool = True, timeout: float | None = None
    ) -> str:
        timeout = timeout or self.timeout
        argv = [self.executable, *(["-P", "--non-interactive"] if global_flags else []), *args]
        kwargs: dict = {}
        env = child_env()
        if env is not None:
            kwargs["env"] = env
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            result = self._runner(
                argv,
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                **kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            output = _text(exc.stdout) + _text(exc.stderr)
            raise LoreCommandError(
                f"lore {' '.join(args[:2])} ran past {timeout:.0f} s", argv, None, output
            ) from exc
        if result.returncode != 0:
            output = (result.stdout or "") + (result.stderr or "")
            if DISCONNECTED in output:
                raise ServerUnreachable(argv, result.returncode, output)
            raise LoreCommandError(
                f"lore {' '.join(args[:2])} failed with exit code {result.returncode}",
                argv,
                result.returncode,
                output,
            )
        return result.stdout or ""


def child_env(environ: dict[str, str] | None = None, prefix: str | None = None) -> dict[str, str] | None:
    """Return the environment for `lore`, or None to inherit it unchanged.

    Drops SSL_CERT_FILE and SSL_CERT_DIR when they point inside the host's own
    Python install, as Blender does with its bundled certifi list. Lore then
    trusts that list instead of the OS store and rejects a sign-in server
    whose certificate only the OS store holds.
    """
    environ = dict(os.environ if environ is None else environ)
    root = os.path.normcase(os.path.abspath(prefix or sys.base_prefix)) + os.sep
    drop = [
        key
        for key in HOST_CA_VARIABLES
        if environ.get(key) and os.path.normcase(os.path.abspath(environ[key])).startswith(root)
    ]
    if not drop:
        return None
    for key in drop:
        del environ[key]
    return environ


def _dotted(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value
