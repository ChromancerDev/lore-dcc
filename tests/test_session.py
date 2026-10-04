"""Session tests against a scripted fake `lore` runner and real temp files."""

from __future__ import annotations

import os
import stat
import subprocess
import threading
from pathlib import Path

import pytest
from lore_dcc import locks as locks_module
from lore_dcc import session as session_module
from lore_dcc.backend.cli import LoreCli
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
BRANCH = "Branch main\n  ID: 7f\n  Latest: " + SIG + "\n  Creator: a@example.com\n"
HEAD = (
    f"On branch main revision 3 -> {SIG}\n"
    f"Remote revision 3 -> {SIG}\n"
    "Local branch in sync with remote\n"
)
VERBS = (
    ("branch", "info"),
    ("status", "--check-dirty"),
    ("lock", "acquire"),
    ("lock", "release"),
    ("lock", "query"),
    ("file", "info"),
    ("file", "reset"),
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

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        args = argv[1:] if argv[1] == "--version" else argv[3:]
        verb = next(v for v in VERBS if tuple(args[: len(v)]) == v)
        name = " ".join(verb)
        rest = args[len(verb) :]
        if verb in (("commit",),):
            shown = rest
        else:
            shown = [Path(a).relative_to(self.root).as_posix() for a in rest]
        self.log.append((name, shown))
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
        if name == "status --check-dirty":
            return self.check_out
        if name == "commit":
            return "Committed revision 4\n"
        if name == "push":
            return "Pushed\n"
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

    def make(self, rel: str, read_only: bool = False) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        mode = stat.S_IREAD if read_only else stat.S_IREAD | stat.S_IWRITE
        os.chmod(path, mode)
        return path

    def lock_query_fails(self) -> None:
        self.runner.fail["lock query"] = (255, "[Error] server unavailable\n")


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def writable(path: Path) -> bool:
    return bool(os.stat(path).st_mode & stat.S_IWRITE)


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


def test_lock_stages_untracked_and_clears_read_only(env: Env) -> None:
    tracked = env.make("a.hip", read_only=True)
    new = env.make("new.hip")
    env.runner.info = {"a.hip": "-", "new.hip": "Added"}
    states = env.session.lock(["a.hip", "new.hip"])
    assert env.runner.calls("lock acquire") == [["a.hip", "new.hip"]]
    assert env.runner.calls("stage") == [["new.hip"]]
    assert writable(tracked)
    assert writable(new)
    assert env.session.touched == {"a.hip", "new.hip"}
    assert [s.path for s in states] == ["a.hip", "new.hip"]


def test_lock_reads_file_info_before_acquiring(env: Env) -> None:
    env.make("a.hip", read_only=True)
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


def test_release_reapplies_read_only(env: Env) -> None:
    path = env.make("a.hip")
    env.runner.info = {"a.hip": "-"}
    env.runner.locks = [("a.hip", "Alice")]
    env.session.release(["a.hip"])
    assert env.runner.calls("lock release") == [["a.hip"]]
    assert not writable(path)


def test_discard_resets_releases_only_own_locks_and_reapplies(env: Env) -> None:
    mine = env.make("a.hip")
    theirs = env.make("b.hip", read_only=True)
    env.runner.info = {"a.hip": "Modified", "b.hip": "-"}
    env.runner.locks = [("a.hip", "Alice"), ("b.hip", "Bob")]
    env.session.touched.update({"a.hip", "b.hip"})
    env.session.discard(["a.hip", "b.hip"])
    assert env.runner.calls("file reset") == [["a.hip", "b.hip"]]
    assert env.runner.calls("lock release") == [["a.hip"]]
    assert not writable(mine)
    assert not writable(theirs)
    assert env.session.touched == set()


def test_discard_with_unknown_locks_does_not_release(env: Env) -> None:
    path = env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.lock_query_fails()
    env.session.discard(["a.hip"])
    assert env.runner.calls("file reset") == [["a.hip"]]
    assert "lock release" not in env.runner.verbs()
    assert writable(path)


def test_discard_refuses_new_files_before_running_lore(env: Env) -> None:
    env.make("a.hip")
    env.make("new.hip")
    env.runner.info = {"a.hip": "Modified", "new.hip": "Added"}
    with pytest.raises(ValueError, match="new.hip is new. Lore has no committed version"):
        env.session.discard(["a.hip", "new.hip"])
    assert "file reset" not in env.runner.verbs()
    assert "lock release" not in env.runner.verbs()


def test_make_writable_and_missing_file(env: Env) -> None:
    path = env.make("a.hip", read_only=True)
    env.session.make_writable(["a.hip", "gone.hip"])
    assert writable(path)
    assert env.runner.log == []


def test_stage_and_mark_saved(env: Env) -> None:
    env.make("a.hip")
    env.session.stage(["a.hip"])
    assert env.runner.calls("stage") == [["a.hip"]]
    env.session.stage([])
    env.session.mark_saved(["a.hip", "gone.hip"])
    assert env.runner.calls("dirty") == [["a.hip"]]
    assert env.session.touched == {"a.hip", "gone.hip"}
    assert env.runner.verbs().count("stage") == 1


def test_refresh_read_only_rule(env: Env) -> None:
    free = env.make("free.hip")
    mine = env.make("mine.hip", read_only=True)
    other = env.make("other.hip")
    new = env.make("new.hip")
    plain = env.make("notes.txt")
    env.runner.info = {
        "free.hip": "-", "mine.hip": "-", "other.hip": "-", "new.hip": "Added", "notes.txt": "-",
    }  # fmt: skip
    env.runner.locks = [("mine.hip", "Alice"), ("other.hip", "Bob")]
    env.session.refresh_read_only(
        ["free.hip", "mine.hip", "other.hip", "new.hip", "notes.txt", "gone.hip"]
    )
    assert not writable(free)
    assert writable(mine)
    assert not writable(other)
    assert writable(new)
    assert writable(plain)


def test_refresh_read_only_unknown_leaves_bits_alone(env: Env) -> None:
    ro = env.make("ro.hip", read_only=True)
    rw = env.make("rw.hip")
    env.runner.info = {"ro.hip": "-", "rw.hip": "-"}
    env.lock_query_fails()
    env.session.refresh_read_only(["ro.hip", "rw.hip"])
    assert not writable(ro)
    assert writable(rw)


def test_prepare_write_locks_only_tracked_lockable_not_mine(env: Env) -> None:
    free = env.make("free.bgeo.sc", read_only=True)
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
    assert writable(free)
    assert env.session.touched == {"free.bgeo.sc"}


def test_prepare_write_nothing_to_do_runs_no_acquire(env: Env) -> None:
    env.make("new.bgeo.sc")
    env.runner.info = {"new.bgeo.sc": "Added"}
    assert env.session.prepare_write(["new.bgeo.sc"]) == ()
    assert env.session.prepare_write([]) == ()
    assert "lock acquire" not in env.runner.verbs()


def test_prepare_write_refuses_other_locks_before_acquiring(env: Env) -> None:
    env.make("free.bgeo.sc")
    env.make("theirs.bgeo.sc", read_only=True)
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
    same = env.make("same.bgeo.sc", read_only=True)
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
    assert not writable(same)
    assert env.session.outputs == {"new.bgeo.sc", "changed.bgeo.sc"}
    assert env.session.touched == {"new.bgeo.sc", "changed.bgeo.sc", "same.bgeo.sc"}


def test_finish_write_releases_only_locks_prepare_write_took(env: Env) -> None:
    held_before = env.make("before.bgeo.sc")
    via_lock = env.make("lock.bgeo.sc")
    taken = env.make("taken.bgeo.sc")
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
    assert writable(held_before)
    assert writable(via_lock)
    assert not writable(taken)


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
    mine = env.make("a.hip")
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
        "status --check-dirty", "file info", "lock query", "lock release", "file info", "lock query",
    ]  # fmt: skip
    assert env.runner.calls("stage") == [["a.hip", "b.txt"]]
    assert env.runner.calls("unstage") == [["extra.hip", "newdir"]]
    assert env.runner.calls("commit") == [["Fix it\n\nMore detail."]]
    assert env.runner.calls("lock release") == [["a.hip"]]
    assert (result.committed, result.pushed, result.diverged, result.push_error) == (
        True, True, False, None,
    )  # fmt: skip
    assert "Committed revision 4" in result.output
    assert "Pushed" in result.output
    assert env.session.touched == {"other.hip"}
    assert env.session.outputs == set()
    assert not writable(mine)


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
    assert (result.committed, result.pushed, result.push_error) == (True, False, None)
    assert "push" not in env.runner.verbs()
    assert "lock release" not in env.runner.verbs()
    assert "unstage" not in env.runner.verbs()


def test_commit_keep_locked_skips_release_but_refreshes_read_only(env: Env) -> None:
    path = env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.runner.locks = [("a.hip", "Alice")]
    result = env.session.commit(["a.hip"], "Summary", keep_locked=True)
    assert result.pushed
    assert "lock release" not in env.runner.verbs()
    assert writable(path)


def test_commit_push_diverged_then_retry(env: Env) -> None:
    path = env.make("a.hip")
    env.runner.info = {"a.hip": "Modified"}
    env.runner.locks = [("a.hip", "Alice")]
    env.runner.fail["push"] = (255, "[Error] Branch has diverged, sync to merge remote changes\n")
    result = env.session.commit(["a.hip"], "Summary")
    assert (result.committed, result.pushed, result.diverged) == (True, False, True)
    assert result.push_error is not None
    assert "Branch has diverged" in result.push_error
    assert "Committed revision 4" in result.output
    assert "lock release" not in env.runner.verbs()
    assert env.session.touched == set()
    assert writable(path)

    del env.runner.fail["push"]
    retry = env.session.retry_push()
    assert (retry.committed, retry.pushed, retry.diverged, retry.push_error) == (
        True, True, False, None,
    )  # fmt: skip
    assert env.runner.calls("lock release") == [["a.hip"]]
    assert not writable(path)
    assert env.session.retry_push().pushed
    assert env.runner.calls("lock release") == [["a.hip"]]


def test_post_push_release_follows_each_commit_keep_locked(env: Env) -> None:
    release = env.make("release.hip")
    keep = env.make("keep.hip")
    env.runner.info = {"release.hip": "Modified", "keep.hip": "Modified"}
    env.runner.locks = [("release.hip", "Alice"), ("keep.hip", "Alice")]
    env.runner.fail["push"] = (6, "[Error] Disconnected from server\n")
    env.session.commit(["release.hip"], "One", keep_locked=False)
    env.session.commit(["keep.hip"], "Two", keep_locked=True)
    del env.runner.fail["push"]
    assert env.session.retry_push().pushed
    assert env.runner.calls("lock release") == [["release.hip"]]
    assert not writable(release)
    assert writable(keep)


def test_post_push_release_skips_files_edited_since_the_commit(env: Env) -> None:
    edited = env.make("edited.hip")
    clean = env.make("clean.hip")
    env.runner.info = {"edited.hip": "Modified", "clean.hip": "Modified"}
    env.runner.locks = [("edited.hip", "Alice"), ("clean.hip", "Alice")]
    env.runner.check_out = HEAD + "Changes not staged for commit:\nM edited.hip\n"
    result = env.session.commit(["edited.hip", "clean.hip"], "Summary")
    assert result.pushed
    assert env.runner.calls("status --check-dirty") == [["edited.hip", "clean.hip"]]
    assert env.runner.calls("lock release") == [["clean.hip"]]
    assert writable(edited)
    assert not writable(clean)


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
    assert env.runner.verbs() == ["push"]


def test_commit_push_other_failure_is_not_diverged(env: Env) -> None:
    env.make("a.txt")
    env.runner.info = {"a.txt": "-"}
    env.runner.fail["push"] = (6, "[Error] Disconnected from server\n")
    result = env.session.commit(["a.txt"], "Summary")
    assert (result.committed, result.pushed, result.diverged) == (True, False, False)
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
