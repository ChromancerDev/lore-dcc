"""Tests for the pure Commit dialog model."""

from __future__ import annotations

import time

import pytest
from lore_dcc import commit_model as cm
from lore_dcc import locks as locks_module
from lore_dcc.backend.parsers import Change, RemoteState, Status
from lore_dcc.locks import LockState
from lore_dcc.session import CommitResult, FileState


def make_status(staged=(), unstaged=(), untracked=(), note=None) -> Status:
    return Status(
        branch="main",
        revision=3,
        remote_revision=3,
        remote_note=note,
        staged=tuple(staged),
        unstaged=tuple(unstaged),
        untracked=tuple(untracked),
    )


def change(code: str, path: str, is_dir: bool = False) -> Change:
    return Change(code, path, is_dir)


def fs(path, lock=LockState.NOT_LOCKED, owner=None, lockable=True) -> FileState:
    return FileState(path, True, True, lockable, lock, owner)


def test_collect_changes_counts_every_section_and_skips_dirs() -> None:
    status = make_status(
        staged=[change("A", "new.hip")],
        unstaged=[change("M", "a.hip"), change("D", "gone.hip")],
        untracked=[change("A", "dir", is_dir=True), change("A", "u.bgeo")],
    )
    assert cm.collect_changes(status) == {
        "new.hip": "A",
        "a.hip": "M",
        "gone.hip": "D",
        "u.bgeo": "A",
    }


def test_collect_changes_merges_codes_for_one_path() -> None:
    status = make_status(
        staged=[change("A", "x"), change("A", "y"), change("D", "z")],
        unstaged=[change("M", "x"), change("D", "y"), change("M", "z")],
        untracked=[change("A", "z")],
    )
    assert cm.collect_changes(status) == {"x": "A", "y": "M", "z": "M"}


def test_groups_and_defaults() -> None:
    status = make_status(
        unstaged=[change("M", "s.hip"), change("M", "o.bgeo"), change("M", "other.txt")],
        untracked=[change("A", "both.hip")],
    )
    rows = cm.build_rows(status, [], touched={"s.hip", "both.hip"}, outputs={"o.bgeo", "both.hip"})
    assert [(r.path, r.group, r.checked) for r in rows] == [
        ("s.hip", cm.GROUP_SESSION, True),
        ("both.hip", cm.GROUP_SESSION, True),
        ("o.bgeo", cm.GROUP_OUTPUTS, True),
        ("other.txt", cm.GROUP_OTHER, False),
    ]
    assert all(r.enabled and r.reason == "" for r in rows)


def test_group_order_is_fixed_regardless_of_status_order() -> None:
    status = make_status(
        unstaged=[change("M", "z"), change("M", "b"), change("M", "o"), change("M", "t")]
    )
    states = [fs("b", LockState.LOCKED_BY_OTHER, "Ann")]
    rows = cm.build_rows(status, states, touched={"t"}, outputs={"o"})
    assert [r.group for r in rows] == [
        cm.GROUP_SESSION,
        cm.GROUP_OUTPUTS,
        cm.GROUP_OTHER,
        cm.GROUP_BLOCKED,
    ]


def new(path: str) -> FileState:
    return FileState(path, True, False, True, LockState.NOT_LOCKED, None)


def test_dependencies_group_sits_between_outputs_and_other_changes() -> None:
    assert cm.GROUPS == (
        cm.GROUP_SESSION,
        cm.GROUP_OUTPUTS,
        cm.GROUP_DEPENDENCIES,
        cm.GROUP_OTHER,
        cm.GROUP_BLOCKED,
    )


def test_untracked_reference_goes_to_dependencies_checked() -> None:
    status = make_status(untracked=[change("A", "ref.bgeo"), change("A", "other.txt")])
    rows = cm.build_rows(status, [new("ref.bgeo")], set(), set(), references=["ref.bgeo"])
    assert [(r.path, r.group, r.checked) for r in rows] == [
        ("ref.bgeo", cm.GROUP_DEPENDENCIES, True),
        ("other.txt", cm.GROUP_OTHER, False),
    ]


def test_tracked_reference_stays_in_other_changes() -> None:
    status = make_status(unstaged=[change("M", "ref.bgeo")])
    rows = cm.build_rows(status, [fs("ref.bgeo")], set(), set(), references=["ref.bgeo"])
    assert [(r.group, r.checked) for r in rows] == [(cm.GROUP_OTHER, False)]


def test_session_and_outputs_win_over_dependencies() -> None:
    status = make_status(untracked=[change("A", "t.bgeo"), change("A", "o.bgeo")])
    states = [new("t.bgeo"), new("o.bgeo")]
    rows = cm.build_rows(status, states, {"t.bgeo"}, {"o.bgeo"}, references=["t.bgeo", "o.bgeo"])
    assert [r.group for r in rows] == [cm.GROUP_SESSION, cm.GROUP_OUTPUTS]


def test_blocked_reference_goes_to_cannot_commit() -> None:
    status = make_status(untracked=[change("A", "ref.bgeo")])
    blocked = FileState("ref.bgeo", True, False, True, LockState.LOCKED_BY_OTHER, "Ann")
    rows = cm.build_rows(status, [blocked], set(), set(), references=["ref.bgeo"])
    assert [r.group for r in rows] == [cm.GROUP_BLOCKED]


def test_blocked_by_other_goes_only_to_cannot_commit() -> None:
    status = make_status(unstaged=[change("M", "a.hip")])
    states = [fs("a.hip", LockState.LOCKED_BY_OTHER, "Ann")]
    rows = cm.build_rows(status, states, touched={"a.hip"}, outputs={"a.hip"})
    assert len(rows) == 1
    row = rows[0]
    assert row.group == cm.GROUP_BLOCKED
    assert (row.checked, row.enabled) == (False, False)
    assert row.reason == "Locked by Ann"
    assert row.lock_owner == "Ann"
    assert row.lock_text == "Locked by Ann"


def test_blocked_unknown_lock_state() -> None:
    status = make_status(unstaged=[change("M", "a.hip")])
    rows = cm.build_rows(status, [fs("a.hip", LockState.UNKNOWN)], set(), set())
    assert rows[0].group == cm.GROUP_BLOCKED
    assert rows[0].reason == "Lock state unknown"
    assert rows[0].enabled is False


def test_unknown_lock_on_unlockable_file_is_not_blocked() -> None:
    status = make_status(unstaged=[change("M", "a.txt")])
    rows = cm.build_rows(status, [fs("a.txt", LockState.UNKNOWN, lockable=False)], set(), set())
    assert rows[0].group == cm.GROUP_OTHER
    assert rows[0].lock_text == ""


def test_locked_by_me_shows_and_commits() -> None:
    status = make_status(unstaged=[change("M", "a.hip")])
    rows = cm.build_rows(status, [fs("a.hip", LockState.LOCKED_BY_ME, "Me")], {"a.hip"}, set())
    assert rows[0].enabled and rows[0].checked
    assert rows[0].lock_text == "Locked by you"


def test_missing_state_is_not_blocked() -> None:
    status = make_status(unstaged=[change("M", "a.hip")])
    rows = cm.build_rows(status, [], set(), set())
    assert rows[0].group == cm.GROUP_OTHER
    assert rows[0].lock_owner is None


def test_status_words_and_codes() -> None:
    status = make_status(unstaged=[change("M", "m"), change("D", "d")], untracked=[change("A", "a")])
    rows = cm.build_rows(status, [], set(), set())
    assert {r.path: (r.code, r.status_word) for r in rows} == {
        "m": ("M", "Modified"),
        "d": ("D", "Deleted"),
        "a": ("A", "Added"),
    }


def test_labels_avoid_perforce_words() -> None:
    banned = ("submit", "changelist", "check in", "get latest", "checkout", "revert")
    texts = list(cm.CODE_WORDS.values()) + list(cm.GROUPS) + [cm.SUMMARY_HINT, cm.FILES_HINT]
    texts += [cm.remote_state_text(s) for s in RemoteState]
    for text in texts:
        assert not any(word in text.lower() for word in banned), text


def test_handles_thousands_of_untracked_files_linearly() -> None:
    paths = [f"cache/f{i:05d}.bgeo" for i in range(20000)]
    status = make_status(untracked=[change("A", p) for p in paths])
    states = [fs(p) for p in paths]
    start = time.perf_counter()
    rows = cm.build_rows(status, states, set(), set(paths[::2]))
    elapsed = time.perf_counter() - start
    assert len(rows) == 20000
    assert sum(r.checked for r in rows) == 10000
    assert elapsed < 2.0


class FakeSource:
    def __init__(self, status: Status, states: list[FileState]) -> None:
        self._status = status
        self._states = states
        self.touched = {"a.hip"}
        self.outputs: set[str] = set()
        self.references: dict[str, tuple[str, ...]] = {}
        self.queried: list[list[str]] = []

    def status(self) -> Status:
        return self._status

    def file_states(self, paths):
        self.queried.append(list(paths))
        wanted = set(paths)
        return [s for s in self._states if s.path in wanted]


def test_load_rows_queries_changed_paths_once() -> None:
    status = make_status(unstaged=[change("M", "a.hip")], untracked=[change("A", "d", True)])
    source = FakeSource(status, [fs("a.hip")])
    got_status, rows = cm.load_rows(source)
    assert got_status is status
    assert source.queried == [["a.hip"]]
    assert [r.group for r in rows] == [cm.GROUP_SESSION]


def test_load_rows_skips_file_query_when_clean() -> None:
    source = FakeSource(make_status(), [])
    _, rows = cm.load_rows(source)
    assert rows == [] and source.queried == []


def test_load_rows_adds_an_untracked_reference_status_does_not_list() -> None:
    source = FakeSource(make_status(unstaged=[change("M", "a.hip")]), [fs("a.hip"), new("ref.bgeo")])
    source.references = {"a.hip": ("ref.bgeo",)}
    status, rows = cm.load_rows(source)
    assert source.queried == [["a.hip", "ref.bgeo"]]
    assert status.untracked == ()
    assert [(r.path, r.code, r.group, r.checked) for r in rows] == [
        ("a.hip", "M", cm.GROUP_SESSION, True),
        ("ref.bgeo", "A", cm.GROUP_DEPENDENCIES, True),
    ]


def test_load_rows_does_not_duplicate_a_reference_status_lists() -> None:
    status = make_status(untracked=[change("A", "ref.bgeo")])
    source = FakeSource(status, [new("ref.bgeo")])
    source.references = {"a.hip": ("ref.bgeo",), "b.hip": ("ref.bgeo",)}
    _, rows = cm.load_rows(source)
    assert source.queried == [["ref.bgeo"]]
    assert [r.path for r in rows] == ["ref.bgeo"]


def test_load_rows_skips_references_missing_from_disk_or_tracked() -> None:
    gone = FileState("gone.bgeo", False, False, True, LockState.NOT_LOCKED, None)
    source = FakeSource(make_status(), [gone, fs("tracked.bgeo")])
    source.references = {"a.hip": ("gone.bgeo", "tracked.bgeo")}
    _, rows = cm.load_rows(source)
    assert rows == []


def test_remote_state_text() -> None:
    assert cm.remote_state_text(RemoteState.IN_SYNC) == "In sync with remote"
    assert cm.remote_state_text(RemoteState.BEHIND) == "Behind remote"
    assert cm.remote_state_text(RemoteState.UNKNOWN) == "Remote state unknown"


def test_files_label_and_hints() -> None:
    assert cm.files_label(1) == "1 file"
    assert cm.files_label(0) == "0 files"
    assert cm.files_label(12) == "12 files"
    assert cm.commit_hint("   ", 3) == "A summary is required."
    assert cm.commit_hint("x", 0) == cm.FILES_HINT
    assert cm.commit_hint(" x ", 1) == ""


def result(**kw) -> CommitResult:
    base = {"pushed": False, "diverged": False, "push_error": None, "output": ""}
    return CommitResult(**{**base, **kw})


def test_push_failure_text_uses_first_error_line() -> None:
    text = cm.push_failure_text(result(push_error="\n[Error] Server said no.\nmore detail\n"))
    assert text == (
        "Committed locally. Push failed: [Error] Server said no. "
        "Nothing has reached the server yet."
    )


def test_push_failure_text_without_error() -> None:
    assert "Push failed: unknown error." in cm.push_failure_text(result())


def test_push_failure_text_diverged() -> None:
    text = cm.push_failure_text(result(diverged=True, push_error="[Error] Branch has diverged"))
    assert "behind remote" in text
    assert "lore sync" in text
    assert "safe locally" in text
    assert "Push failed" not in text


def test_already_staged_files_default_to_checked_in_every_group() -> None:
    status = make_status(
        staged=[change("M", "s.hip"), change("M", "o.bgeo"), change("M", "other.txt"), change("A", "d", True)],
        unstaged=[change("M", "unstaged.txt"), change("M", "locked.hip")],
    )
    states = [fs("locked.hip", LockState.LOCKED_BY_OTHER, "Ann")]
    rows = cm.build_rows(status, states, touched={"s.hip"}, outputs={"o.bgeo"})
    assert {r.path: (r.group, r.checked) for r in rows} == {
        "s.hip": (cm.GROUP_SESSION, True),
        "o.bgeo": (cm.GROUP_OUTPUTS, True),
        "other.txt": (cm.GROUP_OTHER, True),
        "unstaged.txt": (cm.GROUP_OTHER, False),
        "locked.hip": (cm.GROUP_BLOCKED, False),
    }


def test_staged_blocked_file_stays_unchecked() -> None:
    status = make_status(staged=[change("M", "a.hip")])
    rows = cm.build_rows(status, [fs("a.hip", LockState.LOCKED_BY_OTHER, "Ann")], set(), set())
    assert (rows[0].group, rows[0].checked, rows[0].enabled) == (cm.GROUP_BLOCKED, False, False)


def test_windows_matching_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(locks_module, "CASE_INSENSITIVE", True)
    status = make_status(
        staged=[change("M", "Staged.TXT")],
        unstaged=[change("M", "SESSION.hip"), change("M", "Out.bgeo"), change("M", "BLOCKED.hip")],
    )
    states = [fs("blocked.hip", LockState.LOCKED_BY_OTHER, "Ann")]
    rows = cm.build_rows(status, states, touched={"session.HIP"}, outputs={"out.BGEO"})
    assert {r.path: (r.group, r.checked) for r in rows} == {
        "SESSION.hip": (cm.GROUP_SESSION, True),
        "Out.bgeo": (cm.GROUP_OUTPUTS, True),
        "Staged.TXT": (cm.GROUP_OTHER, True),
        "BLOCKED.hip": (cm.GROUP_BLOCKED, False),
    }


def test_commit_complete_text_pushed() -> None:
    text = cm.commit_complete_text(
        result(pushed=True, revision=3, pushed_revision=3, branch="main", server_revision=3)
    )
    assert text == "Committed revision 3 and pushed it to main.\nThe server is at revision 3."


def test_commit_complete_text_not_pushed() -> None:
    text = cm.commit_complete_text(result(pushed=False, revision=3, server_revision=2))
    assert text == (
        "Committed revision 3 locally. It has not been pushed, so nothing has reached the "
        "server yet.\nThe server is at revision 2."
    )


def test_commit_complete_text_leaves_out_what_lore_did_not_report() -> None:
    assert cm.commit_complete_text(result(pushed=True)) == "Committed your changes and pushed it."
    assert cm.commit_complete_text(result(pushed=False)).startswith("Committed your changes locally.")
    retried = result(pushed=True, revision=None, pushed_revision=7, branch="main", server_revision=7)
    assert cm.commit_complete_text(retried).startswith("Committed revision 7 and pushed it to main.")
