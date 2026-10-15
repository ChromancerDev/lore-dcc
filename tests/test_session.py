"""Session tests against a scripted fake `lore` runner and real temp files."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest
from lore_dcc import locks as locks_module
from lore_dcc import session as session_module
from lore_dcc.backend.cli import ROOTS_ONLY_TAG, LoreCli
from lore_dcc.errors import (
    LockNotAcquired,
    LoreCommandError,
    LoreError,
    NotAWorkingCopy,
)
from lore_dcc.locks import LockState
from lore_dcc.session import CommitBlocked, Session, session_for
from lore_dcc.settings import load_settings
from lore_dcc.workspace import Workspace

SIG = "9" * 64
REMOTE_SIG = "8" * 64
BEHIND = (
    f"On branch main revision 3 -> {SIG}\n"
    f"Remote revision 4 -> {REMOTE_SIG}\n"
    "Local branch is behind remote\n"
)
BLOCKED = "[Error] File has local changes: {} (incoming size 7 bytes, file system size 20 bytes)\n"
BRANCH = "Branch main\n  ID: 7f\n  Latest: " + SIG + "\n  Creator: a@example.com\n"
HEAD = (
    f"On branch main revision 3 -> {SIG}\n"
    f"Remote revision 3 -> {SIG}\n"
    "Local branch in sync with remote\n"
)
COMMIT_OUT = (
    "Repository 01a01c890d5c78629798bab981f4d893\n"
    "Revision  : 4\n"
    f"Signature : {SIG}\n"
    "Commit succeeded\n"
)
PUSH_OUT = (
    "Local branch is 1 revision(s) ahead of remote, pushing all revisions\n"
    "Repository 01a01c890d5c78629798bab981f4d893\n"
    "Pushed 1 fragment(s), 170.00 bytes\n"
    f"Pushed revision 4 -> {SIG} to branch main\n"
)
VERBS = (
    ("branch", "info"),
    ("status", "--check-dirty"),
    ("status", "--revision-only"),
    ("lock", "acquire"),
    ("lock", "release"),
    ("lock", "query"),
    ("file", "info"),
    ("file", "reset"),
    ("file", "dependency", "list"),
    ("file", "dependency", "add"),
    ("file", "dependency", "remove"),
    ("revision", "diff"),
    ("sync",),
    ("auth", "info"),
    ("status",),
    ("stage",),
    ("unstage",),
    ("dirty",),
    ("commit",),
    ("push",),
    ("--version",),
)


class ScriptRunner:
    """A fake `lore` that answers from state it keeps, and logs every call."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.log: list[tuple[str, list[str]]] = []
        self.info: dict[str, str] = {}
        self.locks: list[tuple[str, str]] = []
        self.lock_branches: dict[str, str] = {}
        self.fold_info = False
        self.refuse: set[str] = set()
        self.status_out = HEAD
        self.check_out = HEAD
        self.fail: dict[str, tuple[int, str]] = {}
        self.version_out = "lore 0.9.0+783\n"
        self.branch_out = BRANCH
        self.deps: dict[str, list[str]] = {}
        self.diff_out = ""
        self.dirty_roots: set[str] = set()

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        args = argv[1:] if argv[1] == "--version" else argv[3:]
        verb = next(v for v in VERBS if tuple(args[: len(v)]) == v)
        name = " ".join(verb)
        rest = args[len(verb) :]
        if verb in (("commit",), ("revision", "diff")):
            shown = rest
        elif verb == ("sync",):
            assert rest[-2:] in (["--dependency-tag", ROOTS_ONLY_TAG], [ROOTS_ONLY_TAG, "--dry-run"])
            shown = [a for a in rest if a not in ("--dependency-tag", ROOTS_ONLY_TAG)]
        else:
            shown = [
                Path(a).relative_to(self.root).as_posix() for a in rest if a != "--tag=houdini"
            ]
        if verb == ("sync",) and "--dry-run" in rest:
            name = "sync --dry-run"
        self.log.append((name, shown))
        hit = [r for r in shown if r in self.dirty_roots] if verb == ("sync",) else []
        if hit:
            out = "".join(BLOCKED.format(r) for r in hit)
            out += "[Error] Local modifications prevent synchronization\n"
            return subprocess.CompletedProcess(argv, 39, out, "")
        if name in self.fail:
            code, out = self.fail[name]
            return subprocess.CompletedProcess(argv, code, out, "")
        return subprocess.CompletedProcess(argv, 0, self.answer(name, shown), "")

    def same(self, a: str, b: str) -> bool:
        return a.casefold() == b.casefold() if self.fold_info else a == b

    def answer(self, name: str, rels: list[str]) -> str:
        if name == "--version":
            return self.version_out
        if name == "branch info":
            return self.branch_out
        if name == "auth info":
            return "ID: abc123\nName: Alice\n"
        if name == "file info":
            blocks = []
            for rel in rels:
                key = next((k for k in self.info if self.same(k, rel)), None)
                if key is not None:
                    blocks.append(
                        f"Path:    {key}\nType:    file\nSize:    1\nMode:    000\n"
                        f"Context: {'0' * 32}\nHash:    {'0' * 64}\nStatus:  {self.info[key]}\n"
                    )
            return "".join(reversed(blocks))
        if name == "file dependency list":
            lines = "".join(f"  {d} [houdini]\n" for d in self.deps.get(rels[0], []))
            return f"{rels[0]}:\n{lines}"
        if name == "file dependency add":
            self.deps.setdefault(rels[0], []).extend(rels[1:])
        if name == "file dependency remove":
            self.deps[rels[0]] = [d for d in self.deps.get(rels[0], []) if d not in rels[1:]]
        if name == "revision diff":
            return self.diff_out
        if name == "lock query":
            if self.locks is None:
                raise AssertionError("handled through fail")
            return "Locks found:\n" + "".join(
                f"{p} by {o} on branch {self.lock_branches.get(p, '7f')}\n" for p, o in self.locks
            )
        if name == "lock acquire":
            held = [r for r in rels if r not in self.refuse]
            return "Lock acquired on files:\n" + "".join(f"{r}\n" for r in held)
        if name == "lock release" and self.locks:
            self.locks = [(p, o) for p, o in self.locks if not (p in rels and o == "Alice")]
        if name == "status":
            return self.status_out
        if name in ("status --check-dirty", "status --revision-only"):
            return self.check_out
        if name == "commit":
            return COMMIT_OUT
        if name == "push":
            return PUSH_OUT
        return ""

    def calls(self, name: str) -> list[list[str]]:
        return [rels for verb, rels in self.log if verb == name]

    def verbs(self) -> list[str]:
        return [verb for verb, _ in self.log]


class Env:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        (self.root / ".lore").mkdir()
        self.runner = ScriptRunner(self.root)
        lore = LoreCli(self.root, executable="lore", runner=self.runner)
        workspace = Workspace(root=self.root, remote_url=None, identity=None)
        self.session = Session(workspace, lore, load_settings(self.root))

    def make(self, rel: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        return path

    def lock_query_fails(self) -> None:
        self.runner.fail["lock query"] = (255, "[Error] server unavailable\n")


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_file_states_order_lock_states_and_missing(env: Env) -> None:
    env.make("a.hip")
    env.make("new.hip")
    env.make("b.hip")
    env.runner.info = {"a.hip": "-", "new.hip": "Added", "b.hip": "Modified"}
    env.runner.locks = [("a.hip", "Alice"), ("b.hip", "Bob")]
    states = env.session.file_states([env.root / "new.hip", "a.hip", "missing.hip", "b.hip"])
    assert [s.path for s in states] == ["new.hip", "a.hip", "missing.hip", "b.hip"]
    new, mine, missing, other = states
    assert (new.exists, new.tracked, new.lock) == (True, False, LockState.NOT_LOCKED)
    assert (mine.tracked, mine.lock, mine.owner) == (True, LockState.LOCKED_BY_ME, "Alice")
    assert (missing.exists, missing.tracked) == (False, False)
    assert (other.lock, other.owner, other.blocked) == (LockState.LOCKED_BY_OTHER, "Bob", True)
    assert env.runner.verbs().count("file info") == 1
    assert env.runner.verbs().count("lock query") == 1


def test_file_states_lock_query_failure_is_unknown(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "-"}
    env.lock_query_fails()
    (state,) = env.session.file_states(["a.hip"])
    assert state.lock is LockState.UNKNOWN
    assert state.owner is None
    assert state.blocked


def test_file_states_unlockable_never_blocked(env: Env) -> None:
    env.make("notes.txt")
    env.runner.info = {"notes.txt": "-"}
    env.lock_query_fails()
    (state,) = env.session.file_states(["notes.txt"])
    assert not state.lockable
    assert not state.blocked


def test_identity_cached_and_empty_input_runs_nothing(env: Env) -> None:
    assert env.session.file_states([]) == []
    assert env.runner.log == []
    env.make("a.hip")
    env.session.file_states(["a.hip"])
    env.session.file_states(["a.hip"])
    assert env.runner.verbs().count("auth info") == 1
    assert env.session.identity().owns("Alice")
    assert env.session.identity().owns("abc123")


def test_lock_stages_untracked(env: Env) -> None:
    env.make("a.hip")
    env.make("new.hip")
    env.runner.info = {"a.hip": "-", "new.hip": "Added"}
    states = env.session.lock(["a.hip", "new.hip"])
    assert env.runner.calls("lock acquire") == [["a.hip", "new.hip"]]
    assert env.runner.calls("stage") == [["new.hip"]]
    assert env.session.touched == {"a.hip", "new.hip"}
    assert [s.path for s in states] == ["a.hip", "new.hip"]


def test_lock_reads_file_info_before_acquiring(env: Env) -> None:
    env.make("a.hip")
    env.runner.fail["file info"] = (255, "[Error] boom\n")
    with pytest.raises(LoreCommandError):
        env.session.lock(["a.hip"])
    assert "lock acquire" not in env.runner.verbs()


def test_lock_not_acquired_raises_before_staging(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Added"}
    env.runner.refuse = {"a.hip"}
    with pytest.raises(LockNotAcquired) as info:
        env.session.lock(["a.hip"])
    assert info.value.paths == ["a.hip"]
    assert "stage" not in env.runner.verbs()
    assert env.session.touched == set()


def test_release_releases_the_lock(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "-"}
    env.runner.locks = [("a.hip", "Alice")]
    env.session.release(["a.hip"])
    assert env.runner.calls("lock release") == [["a.hip"]]


def test_discard_resets_and_releases_only_own_locks(env: Env) -> None:
    env.make("a.hip")
    env.make("b.hip")
    env.runner.info = {"a.hip": "Modified", "b.hip": "-"}
    env.runner.locks = [("a.hip", "Alice"), ("b.hip", "Bob")]
    env.session.touched.update({"a.hip", "b.hip"})
    env.session.discard(["a.hip", "b.hip"])
    assert env.runner.calls("file reset") == [["a.hip", "b.hip"]]
    assert env.runner.calls("lock release") == [["a.hip"]]
    assert env.session.touched == set()


def test_discard_with_unknown_locks_does_not_release(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.lock_query_fails()
    env.session.discard(["a.hip"])
    assert env.runner.calls("file reset") == [["a.hip"]]
    assert "lock release" not in env.runner.verbs()


def test_discard_refuses_new_files_before_running_lore(env: Env) -> None:
    env.make("a.hip")
    env.make("new.hip")
    env.runner.info = {"a.hip": "Modified", "new.hip": "Added"}
    with pytest.raises(ValueError, match="new.hip is new. Lore has no committed version"):
        env.session.discard(["a.hip", "new.hip"])
    assert "file reset" not in env.runner.verbs()
    assert "lock release" not in env.runner.verbs()


@pytest.mark.parametrize("failure", ["other", "unknown"])
def test_stage_refuses_blocked_files(env: Env, failure: str) -> None:
    env.make("a.hip")
    env.make("b.hip")
    env.runner.info = {"a.hip": "-", "b.hip": "-"}
    if failure == "other":
        env.runner.locks = [("b.hip", "Bob")]
    else:
        env.lock_query_fails()
    with pytest.raises(CommitBlocked) as info:
        env.session.stage(["a.hip", "b.hip"])
    assert "b.hip" in info.value.paths
    assert "cannot be staged or committed" in str(info.value)
    assert ("locked by Bob" if failure == "other" else "lock state unknown") in str(info.value)
    assert "stage" not in env.runner.verbs()
    assert env.session.touched == set()


def test_lock_refuses_to_stage_an_untracked_file_with_unknown_lock_state(env: Env) -> None:
    env.make("new.hip")
    env.runner.info = {"new.hip": "Added"}
    env.lock_query_fails()
    with pytest.raises(CommitBlocked):
        env.session.lock(["new.hip"])
    assert "stage" not in env.runner.verbs()


def test_finish_write_refuses_a_blocked_new_file_before_staging_or_releasing(env: Env) -> None:
    env.make("new.bgeo.sc")
    env.make("same.bgeo.sc")
    env.runner.info = {"new.bgeo.sc": "Added", "same.bgeo.sc": "-"}
    env.session.prepare_write(["same.bgeo.sc"])
    env.runner.locks = [("new.bgeo.sc", "Bob"), ("same.bgeo.sc", "Alice")]
    with pytest.raises(CommitBlocked) as info:
        env.session.finish_write(["new.bgeo.sc", "same.bgeo.sc"])
    assert info.value.paths == ["new.bgeo.sc"]
    assert "stage" not in env.runner.verbs()
    assert "lock release" not in env.runner.verbs()


def test_stage_and_mark_saved(env: Env) -> None:
    env.make("a.hip")
    env.session.stage(["a.hip"])
    assert env.runner.calls("stage") == [["a.hip"]]
    env.session.stage([])
    env.session.mark_saved(["a.hip", "gone.hip"])
    assert env.runner.calls("dirty") == [["a.hip"]]
    assert env.session.touched == {"a.hip", "gone.hip"}
    assert env.runner.verbs().count("stage") == 1


def test_prepare_write_locks_only_tracked_lockable_not_mine(env: Env) -> None:
    env.make("free.bgeo.sc")
    env.make("mine.bgeo.sc")
    env.make("new.bgeo.sc")
    env.make("notes.txt")
    env.runner.info = {
        "free.bgeo.sc": "-", "mine.bgeo.sc": "-", "new.bgeo.sc": "Added", "notes.txt": "-",
    }  # fmt: skip
    env.runner.locks = [("mine.bgeo.sc", "Alice")]
    locked = env.session.prepare_write(
        ["free.bgeo.sc", "mine.bgeo.sc", "new.bgeo.sc", "notes.txt", "gone.bgeo.sc"]
    )
    assert locked == ("free.bgeo.sc",)
    assert env.runner.calls("lock acquire") == [["free.bgeo.sc"]]
    assert env.session.touched == {"free.bgeo.sc"}


def test_prepare_write_nothing_to_do_runs_no_acquire(env: Env) -> None:
    env.make("new.bgeo.sc")
    env.runner.info = {"new.bgeo.sc": "Added"}
    assert env.session.prepare_write(["new.bgeo.sc"]) == ()
    assert env.session.prepare_write([]) == ()
    assert "lock acquire" not in env.runner.verbs()


def test_prepare_write_refuses_other_locks_before_acquiring(env: Env) -> None:
    env.make("free.bgeo.sc")
    env.make("theirs.bgeo.sc")
    env.runner.info = {"free.bgeo.sc": "-", "theirs.bgeo.sc": "-"}
    env.runner.locks = [("theirs.bgeo.sc", "Bob")]
    with pytest.raises(LockNotAcquired) as info:
        env.session.prepare_write(["free.bgeo.sc", "theirs.bgeo.sc"])
    assert info.value.paths == ["theirs.bgeo.sc"]
    assert "Bob" in info.value.output
    assert "lock acquire" not in env.runner.verbs()


def test_prepare_write_unknown_lock_state_still_attempts(env: Env) -> None:
    env.make("a.bgeo.sc")
    env.runner.info = {"a.bgeo.sc": "-"}
    env.lock_query_fails()
    assert env.session.prepare_write(["a.bgeo.sc"]) == ("a.bgeo.sc",)


def test_finish_write_three_way_split(env: Env) -> None:
    env.make("new.bgeo.sc")
    env.make("changed.bgeo.sc")
    env.make("same.bgeo.sc")
    env.runner.info = {"new.bgeo.sc": "Added", "changed.bgeo.sc": "-", "same.bgeo.sc": "-"}
    env.session.prepare_write(["changed.bgeo.sc", "same.bgeo.sc"])
    env.runner.locks = [("changed.bgeo.sc", "Alice"), ("same.bgeo.sc", "Alice")]
    env.runner.check_out = (
        HEAD + "Changes not staged for commit:\nM changed.bgeo.sc\n"
        "Untracked files:\nA new.bgeo.sc\n"
    )
    result = env.session.finish_write(
        ["new.bgeo.sc", "changed.bgeo.sc", "same.bgeo.sc", "gone.bgeo.sc"]
    )
    assert result.staged == ("new.bgeo.sc",)
    assert result.changed == ("changed.bgeo.sc",)
    assert result.released == ("same.bgeo.sc",)
    on_disk = ["new.bgeo.sc", "changed.bgeo.sc", "same.bgeo.sc"]
    assert env.runner.calls("dirty") == [on_disk]
    assert env.runner.calls("status --check-dirty") == [on_disk]
    assert env.runner.calls("stage") == [["new.bgeo.sc"]]
    assert env.runner.calls("lock release") == [["same.bgeo.sc"]]
    assert env.session.outputs == {"new.bgeo.sc", "changed.bgeo.sc"}
    assert env.session.touched == {"new.bgeo.sc", "changed.bgeo.sc", "same.bgeo.sc"}


def test_finish_write_releases_only_locks_prepare_write_took(env: Env) -> None:
    for name in ("before.bgeo.sc", "lock.bgeo.sc", "taken.bgeo.sc"):
        env.make(name)
    env.runner.info = {"before.bgeo.sc": "-", "lock.bgeo.sc": "-", "taken.bgeo.sc": "-"}
    env.session.lock(["lock.bgeo.sc"])
    env.runner.locks = [("before.bgeo.sc", "Alice"), ("lock.bgeo.sc", "Alice")]
    assert env.session.prepare_write(["before.bgeo.sc", "lock.bgeo.sc", "taken.bgeo.sc"]) == (
        "taken.bgeo.sc",
    )
    env.runner.locks.append(("taken.bgeo.sc", "Alice"))
    result = env.session.finish_write(["before.bgeo.sc", "lock.bgeo.sc", "taken.bgeo.sc"])
    assert result.released == ("taken.bgeo.sc",)
    assert env.runner.calls("lock release") == [["taken.bgeo.sc"]]


def test_taken_locks_are_forgotten_on_release_commit_and_discard(env: Env) -> None:
    for name in ("r.bgeo.sc", "c.bgeo.sc", "d.bgeo.sc"):
        env.make(name)
    env.runner.info = {"r.bgeo.sc": "-", "c.bgeo.sc": "Modified", "d.bgeo.sc": "-"}
    env.session.prepare_write(["r.bgeo.sc", "c.bgeo.sc", "d.bgeo.sc"])
    assert env.session._taken == {"r.bgeo.sc", "c.bgeo.sc", "d.bgeo.sc"}
    env.runner.locks = [(n, "Alice") for n in ("r.bgeo.sc", "c.bgeo.sc", "d.bgeo.sc")]
    env.session.release(["r.bgeo.sc"])
    env.session.commit(["c.bgeo.sc"], "Summary", push=False)
    env.session.discard(["d.bgeo.sc"])
    assert env.session._taken == set()


def test_finish_write_keeps_lock_not_held_by_me_and_drops_stale_output(env: Env) -> None:
    env.make("same.bgeo.sc")
    env.runner.info = {"same.bgeo.sc": "-"}
    env.runner.locks = [("same.bgeo.sc", "Bob")]
    env.session.outputs.add("same.bgeo.sc")
    result = env.session.finish_write(["same.bgeo.sc"])
    assert result == session_module.WriteResult((), (), ())
    assert "lock release" not in env.runner.verbs()
    assert env.session.outputs == set()


def test_finish_write_with_nothing_on_disk_runs_nothing(env: Env) -> None:
    result = env.session.finish_write(["gone.bgeo.sc"])
    assert result == session_module.WriteResult((), (), ())
    assert env.runner.log == []


def test_status_passthrough(env: Env) -> None:
    env.runner.status_out = HEAD + "Untracked files:\nA a.hip\n"
    assert [c.path for c in env.session.status().untracked] == ["a.hip"]


def test_commit_validation(env: Env) -> None:
    with pytest.raises(ValueError):
        env.session.commit(["a.hip"], "   ")
    with pytest.raises(ValueError):
        env.session.commit([], "Summary")
    assert env.runner.log == []


@pytest.mark.parametrize("failure", ["other", "unknown"])
def test_commit_blocked(env: Env, failure: str) -> None:
    env.make("a.hip")
    env.make("b.hip")
    env.runner.info = {"a.hip": "-", "b.hip": "-"}
    if failure == "other":
        env.runner.locks = [("b.hip", "Bob")]
    else:
        env.lock_query_fails()
    with pytest.raises(CommitBlocked) as info:
        env.session.commit(["a.hip", "b.hip"], "Summary")
    assert "b.hip" in info.value.paths
    assert "stage" not in env.runner.verbs()
    assert "commit" not in env.runner.verbs()


def test_commit_stages_unstages_others_commits_pushes_and_releases(env: Env) -> None:
    env.make("a.hip")
    env.make("b.txt")
    env.runner.info = {"a.hip": "Modified", "b.txt": "-"}
    env.runner.locks = [("a.hip", "Alice")]
    env.runner.status_out = (
        HEAD + "Changes staged for commit:\nM a.hip\nM extra.hip\nA newdir/\nA b.txt\n"
    )
    env.session.touched.update({"a.hip", "b.txt", "other.hip"})
    env.session.outputs.add("a.hip")
    result = env.session.commit(["a.hip", "b.txt"], "  Fix it  ", "More detail.")
    assert env.runner.verbs() == [
        "auth info", "file info", "lock query", "branch info",
        "stage", "status", "unstage", "commit", "push",
        "status --check-dirty", "file info", "lock query", "lock release",
        "status --revision-only",
    ]  # fmt: skip
    assert env.runner.calls("stage") == [["a.hip", "b.txt"]]
    assert env.runner.calls("unstage") == [["extra.hip", "newdir"]]
    assert env.runner.calls("commit") == [["Fix it\n\nMore detail."]]
    assert env.runner.calls("lock release") == [["a.hip"]]
    assert (result.pushed, result.diverged, result.push_error) == (True, False, None)
    assert "Commit succeeded" in result.output
    assert "Pushed revision 4" in result.output
    assert (result.revision, result.pushed_revision, result.branch) == (4, 4, "main")
    assert result.server_revision == 3
    assert env.session.touched == {"other.hip"}
    assert env.session.outputs == set()


def test_commit_unstages_only_folders_without_included_files(env: Env) -> None:
    env.make("scenes/new/a.hip")
    env.runner.info = {"scenes/new/a.hip": "Added"}
    env.runner.status_out = (
        HEAD
        + "Changes staged for commit:\nA scenes/ \nA scenes/new/ \nA scenes/new/a.hip \n"
        + "A caches/ \nA caches/b.bgeo.sc \n"
    )
    env.session.commit(["scenes/new/a.hip"], "Add a", push=False)
    assert env.runner.calls("unstage") == [["caches", "caches/b.bgeo.sc"]]


def test_commit_summary_only_message(env: Env) -> None:
    env.make("a.txt")
    env.runner.info = {"a.txt": "-"}
    env.session.commit(["a.txt"], "Just a summary", push=False)
    assert env.runner.calls("commit") == [["Just a summary"]]


def test_commit_without_push_keeps_locks(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.runner.locks = [("a.hip", "Alice")]
    result = env.session.commit(["a.hip"], "Summary", push=False)
    assert (result.pushed, result.push_error) == (False, None)
    assert (result.revision, result.pushed_revision, result.branch) == (4, None, None)
    assert result.server_revision == 3
    assert "push" not in env.runner.verbs()
    assert "lock release" not in env.runner.verbs()
    assert "unstage" not in env.runner.verbs()


def test_commit_keep_locked_skips_release(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.runner.locks = [("a.hip", "Alice")]
    result = env.session.commit(["a.hip"], "Summary", keep_locked=True)
    assert result.pushed
    assert "lock release" not in env.runner.verbs()


def test_commit_push_diverged_then_retry(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.runner.locks = [("a.hip", "Alice")]
    env.runner.fail["push"] = (255, "[Error] Branch has diverged, sync to merge remote changes\n")
    result = env.session.commit(["a.hip"], "Summary")
    assert (result.pushed, result.diverged) == (False, True)
    assert result.push_error is not None
    assert "Branch has diverged" in result.push_error
    assert "Commit succeeded" in result.output
    assert (result.revision, result.pushed_revision, result.branch) == (4, None, None)
    assert "lock release" not in env.runner.verbs()
    assert env.session.touched == set()

    del env.runner.fail["push"]
    retry = env.session.retry_push()
    assert (retry.pushed, retry.diverged, retry.push_error) == (True, False, None)
    assert (retry.revision, retry.pushed_revision, retry.branch) == (None, 4, "main")
    assert env.runner.calls("lock release") == [["a.hip"]]
    assert env.session.retry_push().pushed
    assert env.runner.calls("lock release") == [["a.hip"]]


def test_post_push_release_follows_each_commit_keep_locked(env: Env) -> None:
    env.make("release.hip")
    env.make("keep.hip")
    env.runner.info = {"release.hip": "Modified", "keep.hip": "Modified"}
    env.runner.locks = [("release.hip", "Alice"), ("keep.hip", "Alice")]
    env.runner.fail["push"] = (6, "[Error] Disconnected from server\n")
    env.session.commit(["release.hip"], "One", keep_locked=False)
    env.session.commit(["keep.hip"], "Two", keep_locked=True)
    del env.runner.fail["push"]
    assert env.session.retry_push().pushed
    assert env.runner.calls("lock release") == [["release.hip"]]


def test_post_push_release_skips_files_edited_since_the_commit(env: Env) -> None:
    env.make("edited.hip")
    env.make("clean.hip")
    env.runner.info = {"edited.hip": "Modified", "clean.hip": "Modified"}
    env.runner.locks = [("edited.hip", "Alice"), ("clean.hip", "Alice")]
    env.runner.check_out = HEAD + "Changes not staged for commit:\nM edited.hip\n"
    result = env.session.commit(["edited.hip", "clean.hip"], "Summary")
    assert result.pushed
    assert env.runner.calls("status --check-dirty") == [["edited.hip", "clean.hip"]]
    assert env.runner.calls("lock release") == [["clean.hip"]]


def test_post_push_keeps_a_lock_any_commit_asked_to_keep(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.runner.locks = [("a.hip", "Alice")]
    env.session.commit(["a.hip"], "One", push=False, keep_locked=False)
    env.session.commit(["a.hip"], "Two", keep_locked=True)
    assert "lock release" not in env.runner.verbs()


def test_post_push_clears_pending_commits(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.runner.locks = [("a.hip", "Alice")]
    env.session.commit(["a.hip"], "One")
    env.runner.log.clear()
    assert env.session.retry_push().pushed
    assert env.runner.verbs() == ["push", "status --revision-only"]


def test_commit_push_other_failure_is_not_diverged(env: Env) -> None:
    env.make("a.txt")
    env.runner.info = {"a.txt": "-"}
    env.runner.fail["push"] = (6, "[Error] Disconnected from server\n")
    result = env.session.commit(["a.txt"], "Summary")
    assert (result.pushed, result.diverged) == (False, False)
    assert result.push_error == "[Error] Disconnected from server"


def test_commit_failure_raises_and_keeps_state(env: Env) -> None:
    env.make("a.txt")
    env.runner.info = {"a.txt": "-"}
    env.runner.fail["commit"] = (1, "[Error] Nothing to commit\n")
    env.session.touched.add("a.txt")
    with pytest.raises(LoreCommandError):
        env.session.commit(["a.txt"], "Summary")
    assert "push" not in env.runner.verbs()
    assert env.session.touched == {"a.txt"}
    assert env.session.retry_push().pushed


def test_push_succeeds_even_when_lock_cleanup_fails(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.runner.locks = [("a.hip", "Alice")]
    env.runner.fail["lock release"] = (255, "[Error] boom\n")
    result = env.session.commit(["a.hip"], "Summary")
    assert result.pushed
    assert "lock cleanup failed" in result.output


def test_session_for_caches_per_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    (root / ".lore").mkdir()
    (root / "lore-dcc.json").write_text(
        '{"version": 1, "houdini": {"x": 1}}', encoding="utf-8"
    )
    runner = ScriptRunner(root)
    created: list[LoreCli] = []

    def factory(path: Path) -> LoreCli:
        created.append(LoreCli(path, executable="lore", runner=runner))
        return created[-1]

    monkeypatch.setattr(session_module, "LoreCli", factory)
    monkeypatch.setattr(session_module, "_SESSIONS", {})
    first = session_for(root / "scenes" / "shot.hip")
    second = session_for(root)
    assert first is second
    assert len(created) == 1
    assert runner.verbs() == ["--version"]
    assert first.settings.houdini == {"x": 1}
    assert first.workspace.root == root


def test_session_for_threads_create_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    (root / ".lore").mkdir()
    runner = ScriptRunner(root)
    count = []

    def factory(path: Path) -> LoreCli:
        count.append(path)
        return LoreCli(path, executable="lore", runner=runner)

    monkeypatch.setattr(session_module, "LoreCli", factory)
    monkeypatch.setattr(session_module, "_SESSIONS", {})
    results: list[Session] = []
    threads = [
        threading.Thread(target=lambda: results.append(session_for(root))) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(count) == 1
    assert len({id(r) for r in results}) == 1


def test_session_for_errors_are_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path.resolve()
    (root / ".lore").mkdir()
    runner = ScriptRunner(root)
    runner.version_out = "lore 0.8.0\n"
    monkeypatch.setattr(
        session_module, "LoreCli", lambda p: LoreCli(p, executable="lore", runner=runner)
    )
    monkeypatch.setattr(session_module, "_SESSIONS", {})
    with pytest.raises(LoreError):
        session_for(root)
    runner.version_out = "lore 0.9.0+1\n"
    assert session_for(root).workspace.root == root


def test_session_for_outside_working_copy(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    with pytest.raises(NotAWorkingCopy):
        session_for(folder / "a.hip")


def test_is_staged_reads_status_and_ignores_directory_entries(env: Env) -> None:
    env.runner.status_out = HEAD + "Changes staged for commit:\nA new.hip\nA dir/\n"
    assert env.session.is_staged("new.hip")
    assert not env.session.is_staged("dir")
    assert not env.session.is_staged("other.hip")


def test_locks_on_other_branches_are_ignored(env: Env) -> None:
    env.make("a.hip")
    env.make("b.hip")
    env.runner.info = {"a.hip": "-", "b.hip": "-"}
    env.runner.locks = [("a.hip", "Bob"), ("b.hip", "Bob")]
    env.runner.lock_branches = {"a.hip": "other"}
    mine, theirs = env.session.file_states(["a.hip", "b.hip"])
    assert (mine.lock, mine.owner) == (LockState.NOT_LOCKED, None)
    assert (theirs.lock, theirs.owner) == (LockState.LOCKED_BY_OTHER, "Bob")


def test_branch_id_is_cached_until_invalidated(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "-"}
    env.session.file_states(["a.hip"])
    env.session.file_states(["a.hip"])
    assert env.runner.verbs().count("branch info") == 1
    assert env.runner.verbs().count("auth info") == 1
    env.session.invalidate()
    env.session.file_states(["a.hip"])
    assert env.runner.verbs().count("branch info") == 2
    assert env.runner.verbs().count("auth info") == 2
    assert env.session.branch_id() == "7f"


def test_branch_lookup_failure_counts_every_lock_and_is_not_cached(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "-"}
    env.runner.locks = [("a.hip", "Bob")]
    env.runner.lock_branches = {"a.hip": "other"}
    env.runner.fail["branch info"] = (255, "[Error] boom\n")
    (state,) = env.session.file_states(["a.hip"])
    assert state.lock is LockState.LOCKED_BY_OTHER
    del env.runner.fail["branch info"]
    (state,) = env.session.file_states(["a.hip"])
    assert state.lock is LockState.NOT_LOCKED


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(locks_module, "CASE_INSENSITIVE", True)


def test_windows_file_states_match_lore_spelling_case_insensitively(
    env: Env, windows: None
) -> None:
    env.make("Scenes/A.hip")
    env.runner.fold_info = True
    env.runner.info = {"scenes/a.HIP": "-"}
    env.runner.locks = [("SCENES/a.hip", "Bob")]
    (state,) = env.session.file_states(["Scenes/A.hip"])
    assert state.path == "Scenes/A.hip"
    assert (state.tracked, state.lock, state.owner) == (True, LockState.LOCKED_BY_OTHER, "Bob")


def test_windows_commit_does_not_unstage_a_differently_cased_path(
    env: Env, windows: None
) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.runner.status_out = HEAD + "Changes staged for commit:\nM A.HIP\n"
    env.session.commit(["a.hip"], "Summary", push=False)
    assert "unstage" not in env.runner.verbs()


def test_windows_finish_write_matches_check_dirty_case_insensitively(
    env: Env, windows: None
) -> None:
    env.make("a.bgeo.sc")
    env.runner.info = {"a.bgeo.sc": "-"}
    env.runner.check_out = HEAD + "Changes not staged for commit:\nM A.BGEO.SC\n"
    assert env.session.finish_write(["a.bgeo.sc"]).changed == ("a.bgeo.sc",)


def test_case_sensitive_platforms_keep_exact_matching(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(locks_module, "CASE_INSENSITIVE", False)
    env.make("a.hip")
    env.runner.info = {"A.HIP": "-"}
    env.runner.locks = [("A.hip", "Bob")]
    (state,) = env.session.file_states(["a.hip"])
    assert (state.tracked, state.lock) == (False, LockState.NOT_LOCKED)


def test_server_revision_is_none_when_the_status_call_fails(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.runner.fail["status --revision-only"] = (1, "[Error] no server")
    result = env.session.commit(["a.hip"], "Summary")
    assert result.pushed and result.server_revision is None
    assert (result.revision, result.pushed_revision, result.branch) == (4, 4, "main")


def test_unreadable_commit_and_push_output_leaves_the_revisions_none(env: Env) -> None:
    env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    answer = env.runner.answer
    env.runner.answer = lambda name, rels: "ok" if name in ("commit", "push") else answer(name, rels)
    result = env.session.commit(["a.hip"], "Summary")
    assert result.pushed
    assert (result.revision, result.pushed_revision, result.branch) == (None, None, None)


def staged(*paths: str) -> str:
    return HEAD + "Changes staged for commit:\n" + "".join(f"A {p}\n" for p in paths)


def link_env(env: Env, refs: list[str], scene: str = "scene.hip") -> None:
    env.make(scene)
    env.runner.info = {scene: "-", "tool.hda": "-"}
    env.session.set_references(scene, refs)


def test_set_references_stores_relative_unique_and_replaces(env: Env) -> None:
    env.session.set_references(env.root / "scene.hip", [env.root / "a.bgeo", "a.bgeo", "b.bgeo"])
    assert env.session.references == {"scene.hip": ("a.bgeo", "b.bgeo")}
    env.session.set_references("scene.hip", ["c.bgeo"])
    assert env.session.references == {"scene.hip": ("c.bgeo",)}
    assert env.runner.log == []


def test_windows_set_references_replaces_a_differently_cased_scene(
    env: Env, windows: None
) -> None:
    env.session.set_references("Scene.hip", ["a.bgeo"])
    env.session.set_references("scene.HIP", ["b.bgeo"])
    assert env.session.references == {"scene.HIP": ("b.bgeo",)}


def test_update_links_without_remembered_references_runs_nothing(env: Env) -> None:
    env.session.update_links("scene.hip")
    assert env.runner.log == []


def test_update_links_adds_only_tracked_or_staged_existing_references(env: Env) -> None:
    link_env(env, ["tool.hda", "new.bgeo", "fresh.bgeo", "gone.bgeo"])
    for name in ("tool.hda", "new.bgeo", "fresh.bgeo"):
        env.make(name)
    env.runner.info["gone.bgeo"] = "-"
    env.runner.status_out = staged("new.bgeo")
    env.session.update_links("scene.hip")
    assert env.runner.calls("file dependency add") == [["scene.hip", "tool.hda", "new.bgeo"]]
    assert "file dependency remove" not in env.runner.verbs()
    assert env.runner.verbs().count("file info") == 1


def test_update_links_skips_references_already_linked(env: Env) -> None:
    link_env(env, ["tool.hda"])
    env.make("tool.hda")
    env.runner.deps = {"scene.hip": ["tool.hda"]}
    env.session.update_links("scene.hip")
    assert "file dependency add" not in env.runner.verbs()


def test_update_links_removes_links_the_scene_no_longer_reads(env: Env) -> None:
    link_env(env, ["tool.hda"])
    env.make("tool.hda")
    env.runner.deps = {"scene.hip": ["tool.hda", "old.hda"]}
    env.session.update_links("scene.hip")
    assert env.runner.calls("file dependency remove") == [["scene.hip", "old.hda"]]
    assert "file dependency add" not in env.runner.verbs()


def test_update_links_with_an_empty_list_removes_every_link(env: Env) -> None:
    link_env(env, [])
    env.runner.deps = {"scene.hip": ["old.hda"]}
    env.session.update_links("scene.hip")
    assert env.runner.calls("file dependency remove") == [["scene.hip", "old.hda"]]


def test_update_links_on_an_untracked_unstaged_scene_does_nothing(env: Env) -> None:
    link_env(env, ["tool.hda"])
    env.runner.info = {"tool.hda": "-"}
    env.make("tool.hda")
    env.session.update_links("scene.hip")
    assert not [v for v in env.runner.verbs() if v.startswith("file dependency")]


def test_update_links_accepts_a_staged_new_scene(env: Env) -> None:
    link_env(env, ["tool.hda"])
    env.runner.info = {"tool.hda": "-"}
    env.runner.status_out = staged("scene.hip")
    env.make("tool.hda")
    env.session.update_links("scene.hip")
    assert env.runner.calls("file dependency add") == [["scene.hip", "tool.hda"]]


def test_windows_update_links_finds_the_scene_case_insensitively(env: Env, windows: None) -> None:
    link_env(env, ["tool.hda"])
    env.make("tool.hda")
    env.runner.fold_info = True
    env.session.update_links("SCENE.HIP")
    (call,) = env.runner.calls("file dependency add")
    assert [p.casefold() for p in call] == ["scene.hip", "tool.hda"]


def test_stage_updates_links_of_a_remembered_scene(env: Env) -> None:
    link_env(env, ["tool.hda"])
    env.make("tool.hda")
    env.runner.status_out = staged("scene.hip")
    env.session.stage(["scene.hip"])
    assert env.runner.verbs().index("stage") < env.runner.verbs().index("file dependency add")
    assert env.runner.calls("file dependency add") == [["scene.hip", "tool.hda"]]


def test_stage_without_remembered_references_adds_no_links(env: Env) -> None:
    env.make("a.hip")
    env.session.stage(["a.hip"])
    assert not [v for v in env.runner.verbs() if v.startswith("file dependency")]
    assert env.runner.calls("stage") == [["a.hip"]]


def test_commit_links_untracked_references_that_are_included(env: Env) -> None:
    link_env(env, ["fresh.bgeo"])
    env.make("fresh.bgeo")
    env.runner.status_out = staged("fresh.bgeo")
    env.session.commit(["scene.hip", "fresh.bgeo"], "Summary", push=False)
    verbs = env.runner.verbs()
    assert verbs.index("stage") < verbs.index("file dependency add") < verbs.index("commit")
    assert env.runner.calls("file dependency add") == [["scene.hip", "fresh.bgeo"]]


def test_commit_gives_no_link_to_an_unchecked_untracked_reference(env: Env) -> None:
    link_env(env, ["fresh.bgeo"])
    env.make("fresh.bgeo")
    env.session.commit(["scene.hip"], "Summary", push=False)
    assert "file dependency add" not in env.runner.verbs()


def test_behind_stops_when_the_signatures_match(env: Env) -> None:
    env.session.set_references("scene.hip", ["a.bgeo"])
    assert env.session.behind("scene.hip") == session_module.BehindReport(False, ())
    assert env.runner.verbs() == ["status --revision-only"]


def test_behind_stops_when_the_remote_signature_is_missing(env: Env) -> None:
    env.runner.check_out = f"On branch main revision 3 -> {SIG}\n"
    assert env.session.behind("scene.hip") == session_module.BehindReport(False, ())
    assert "revision diff" not in env.runner.verbs()


def test_behind_lists_changed_references_in_scene_order_and_flags_the_scene(env: Env) -> None:
    env.session.set_references("scene.hip", ["a.bgeo", "b.bgeo", "c.bgeo"])
    env.runner.check_out = BEHIND
    env.runner.diff_out = "M scene.hip\nM c.bgeo\nM a.bgeo\nA other/\nM x.bgeo\n"
    report = env.session.behind("scene.hip")
    assert report == session_module.BehindReport(True, ("a.bgeo", "c.bgeo"))
    assert env.runner.calls("revision diff") == [[SIG, "--target", REMOTE_SIG]]


def test_behind_without_changes_to_the_scene_or_references(env: Env) -> None:
    env.session.set_references("scene.hip", ["a.bgeo"])
    env.runner.check_out = BEHIND
    env.runner.diff_out = "M x.bgeo\n"
    assert env.session.behind("scene.hip") == session_module.BehindReport(False, ())


def test_sync_references_syncs_every_root_when_the_dry_run_passes(env: Env) -> None:
    result = env.session.sync_references(["a.hip", "b.bgeo"])
    assert result.synced == ("a.hip", "b.bgeo")
    assert result.skipped == ()
    roots = ["--root-file", "a.hip", "--root-file", "b.bgeo"]
    assert env.runner.calls("sync --dry-run") == [[*roots, "--dry-run"]]
    assert env.runner.calls("sync") == [roots]


def test_sync_references_drops_locally_changed_roots_and_retries(env: Env) -> None:
    env.runner.dirty_roots = {"b.bgeo"}
    result = env.session.sync_references(["a.bgeo", "b.bgeo"])
    assert (result.synced, result.skipped) == (("a.bgeo",), ("b.bgeo",))
    dry = env.runner.calls("sync --dry-run")
    assert [r.count("--root-file") for r in dry] == [2, 1]
    assert env.runner.calls("sync") == [["--root-file", "a.bgeo"]]


def test_sync_references_raises_when_the_refusal_names_no_root(env: Env) -> None:
    env.runner.fail["sync --dry-run"] = (39, BLOCKED.format("other.bgeo"))
    with pytest.raises(LoreCommandError):
        env.session.sync_references(["a.bgeo"])
    assert "sync" not in env.runner.verbs()


def test_sync_references_with_every_root_blocked_does_not_sync(env: Env) -> None:
    env.runner.dirty_roots = {"a.bgeo", "b.bgeo"}
    result = env.session.sync_references(["a.bgeo", "b.bgeo"])
    assert result == session_module.SyncResult((), ("a.bgeo", "b.bgeo"))
    assert "sync" not in env.runner.verbs()


def test_update_links_removes_a_link_to_a_reference_no_longer_staged(env: Env) -> None:
    link_env(env, ["new.bgeo"])
    env.make("new.bgeo")
    env.runner.deps = {"scene.hip": ["new.bgeo"]}
    env.session.update_links("scene.hip")
    assert env.runner.calls("file dependency remove") == [["scene.hip", "new.bgeo"]]


def test_set_references_forgets_the_previous_scene(env: Env) -> None:
    env.session.set_references("a.hip", ["a.bgeo"])
    env.session.set_references("b.hip", ["b.bgeo"])
    assert env.session.references == {"b.hip": ("b.bgeo",)}


@pytest.mark.parametrize(
    "note", ["Local branch has diverged, synchronize to merge", "Local branch in sync with remote"]
)
def test_behind_skips_a_branch_that_is_not_behind(env: Env, note: str) -> None:
    env.session.set_references("scene.hip", ["a.bgeo"])
    env.runner.check_out = BEHIND.replace("Local branch is behind remote", note)
    env.runner.diff_out = "M a.bgeo\n"
    assert env.session.behind("scene.hip") == session_module.BehindReport(False, ())
    assert "revision diff" not in env.runner.verbs()


def test_session_for_keeps_one_session_per_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    (root / ".lore").mkdir()
    runner = ScriptRunner(root)
    monkeypatch.setattr(
        session_module, "LoreCli", lambda p: LoreCli(p, executable="lore", runner=runner)
    )
    monkeypatch.setattr(session_module, "_SESSIONS", {})
    houdini = session_for(root / "a.hip")
    blender = session_for(root / "a.blend", session_module.BLENDER)
    assert houdini is not blender
    assert blender is session_for(root, session_module.BLENDER)
    assert (houdini.lore.link_tag, blender.lore.link_tag) == ("houdini", "blender")
    assert blender.settings.is_lockable("a.blend")
    assert not houdini.settings.is_lockable("a.blend")
