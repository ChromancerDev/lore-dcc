"""Commit and Info dialogs, built against fake sessions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from lore_dcc import prefs, ui
from lore_dcc.backend.parsers import Change, Status
from lore_dcc.errors import LoreCommandError
from lore_dcc.locks import LockState
from lore_dcc.session import CommitBlocked, CommitResult, FileState
from lore_dcc.ui import dialogs
from PySide6 import QtCore, QtWidgets

CHECKED = QtCore.Qt.CheckState.Checked
UNCHECKED = QtCore.Qt.CheckState.Unchecked
PARTIAL = QtCore.Qt.CheckState.PartiallyChecked


@pytest.fixture(autouse=True)
def shown(monkeypatch) -> list[tuple[str, str]]:
    """Capture show_message calls so no test blocks on a modal box."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(dialogs, "show_message", lambda title, text: calls.append((title, text)))
    return calls


def make_status(note: str | None = "Local branch in sync with remote", **kw) -> Status:
    return Status(
        branch="main",
        revision=7,
        remote_revision=7,
        remote_note=note,
        staged=(),
        unstaged=tuple(Change("M", p, False) for p in kw.get("unstaged", ())),
        untracked=tuple(Change("A", p, False) for p in kw.get("untracked", ())),
    )


def state(path, lock=LockState.NOT_LOCKED, owner=None) -> FileState:
    return FileState(path, True, True, True, lock, owner)


def outcome(**kw) -> CommitResult:
    base = {
        "pushed": True,
        "diverged": False,
        "push_error": None,
        "output": "",
        "revision": 4,
        "pushed_revision": 4,
        "branch": "main",
        "server_revision": 4,
    }
    return CommitResult(**{**base, **kw})


class FakeLore:
    def __init__(self, fail_auth: bool = False) -> None:
        self.fail_auth = fail_auth

    def auth_info(self):
        if self.fail_auth:
            raise LoreCommandError("auth failed", ["lore", "auth", "info"], 1, "not logged in")
        return SimpleNamespace(user_id="u1", name="Ben")

    def version(self):
        return (0, 9, 0)


class FakeSession:
    def __init__(self, status=None, states=(), commit_result=None, fail_auth=False) -> None:
        self._status = status or make_status(
            unstaged=["s1.hip", "s2.hip", "o.bgeo", "x.txt", "b.hip"]
        )
        self._states = list(states) or [state("b.hip", LockState.LOCKED_BY_OTHER, "Ann")]
        self.touched = {"s1.hip", "s2.hip"}
        self.outputs = {"o.bgeo"}
        self.references: dict[str, tuple[str, ...]] = {}
        self.workspace = SimpleNamespace(root=Path("/work/repo"), remote_url="lore://host/repo")
        self.lore = FakeLore(fail_auth)
        self.commit_result = commit_result or outcome()
        self.retry_results = [outcome()]
        self.commit_calls: list[tuple] = []
        self.retry_calls: list[tuple] = []
        self.commit_error: Exception | None = None

    def status(self):
        return self._status

    def file_states(self, paths):
        wanted = set(paths)
        return [s for s in self._states if s.path in wanted]

    def commit(self, include, summary, description="", push=True, keep_locked=False):
        self.commit_calls.append((list(include), summary, description, push, keep_locked))
        if self.commit_error:
            raise self.commit_error
        return self.commit_result

    def retry_push(self):
        self.retry_calls.append(())
        return self.retry_results.pop(0)


def build(session=None, user_prefs=None) -> dialogs._CommitDialog:
    session = session or FakeSession()
    status, rows = dialogs.commit_model.load_rows(session)
    return dialogs._CommitDialog(session, status, rows, user_prefs or prefs.Prefs())


def group(dlg, name):
    return next(g for g in dlg._group_items if g.text(0).startswith(name))


def test_groups_rows_and_defaults(qapp) -> None:
    dlg = build()
    names = [g.text(0) for g in dlg._group_items]
    assert names == ["This session (2)", "Outputs (1)", "Other changes (1)", "Cannot commit (1)"]
    assert sorted(dlg.checked_paths()) == ["o.bgeo", "s1.hip", "s2.hip"]
    assert group(dlg, "This session").checkState(0) == CHECKED
    assert group(dlg, "Other changes").checkState(0) == UNCHECKED
    assert dlg.count_label.text() == "3 files"


def test_cannot_commit_rows_are_disabled_with_reason(qapp) -> None:
    dlg = build()
    blocked = group(dlg, "Cannot commit")
    row = blocked.child(0)
    assert not row.flags() & QtCore.Qt.ItemFlag.ItemIsEnabled
    assert row.text(1) == "b.hip"
    assert row.text(2) == "Locked by Ann"
    assert row.toolTip(2) == "Locked by Ann"
    assert not blocked.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable
    assert "b.hip" not in dlg.checked_paths()


def test_columns_and_status_words(qapp) -> None:
    dlg = build()
    assert [dlg.tree.headerItem().text(i) for i in range(3)] == ["Status", "Path", "Lock"]
    assert group(dlg, "This session").child(0).text(0) == "Modified"


def test_group_checkbox_is_tristate(qapp) -> None:
    dlg = build()
    session_group = group(dlg, "This session")
    session_group.child(0).setCheckState(0, UNCHECKED)
    assert session_group.checkState(0) == PARTIAL
    assert dlg.count_label.text() == "2 files"
    session_group.setCheckState(0, UNCHECKED)
    assert [session_group.child(i).checkState(0) for i in range(2)] == [UNCHECKED, UNCHECKED]
    session_group.setCheckState(0, CHECKED)
    assert [session_group.child(i).checkState(0) for i in range(2)] == [CHECKED, CHECKED]
    other = group(dlg, "Other changes")
    other.setCheckState(0, CHECKED)
    assert "x.txt" in dlg.checked_paths()
    assert dlg.count_label.text() == "4 files"


def test_commit_disabled_until_summary_with_hint(qapp) -> None:
    dlg = build()
    assert not dlg.commit_button.isEnabled()
    assert dlg.hint_label.text() == "A summary is required."
    dlg.summary_edit.setText("   ")
    assert not dlg.commit_button.isEnabled()
    dlg.summary_edit.setText("Fix rig")
    assert dlg.commit_button.isEnabled()
    assert dlg.hint_label.text() == ""
    for g in dlg._group_items:
        g.setCheckState(0, UNCHECKED)
    assert not dlg.commit_button.isEnabled()
    assert dlg.count_label.text() == "0 files"


def test_top_line_shows_branch_and_remote_state(qapp) -> None:
    dlg = build()
    assert dlg.branch_label.text() == "Branch main  |  In sync with remote"
    behind = build(FakeSession(make_status("Local branch is behind remote", unstaged=["a.hip"])))
    assert "Behind remote" in behind.branch_label.text()
    unknown = build(FakeSession(make_status(None, unstaged=["a.hip"])))
    assert "Remote state unknown" in unknown.branch_label.text()


def test_behind_remote_does_not_block_commit(qapp) -> None:
    dlg = build(FakeSession(make_status("Local branch is behind remote", unstaged=["a.hip"])))
    dlg.summary_edit.setText("x")
    group(dlg, "Other changes").setCheckState(0, CHECKED)
    assert dlg.commit_button.isEnabled()


def test_option_defaults_come_from_prefs(qapp) -> None:
    dlg = build(user_prefs=prefs.Prefs(push_after_commit=False, keep_files_locked=True))
    assert not dlg.push_box.isChecked()
    assert dlg.keep_box.isChecked()
    default = build()
    assert default.push_box.isChecked() and not default.keep_box.isChecked()
    assert default.push_box.toolTip() == (
        "Commit records a revision locally. Push sends it to the server. "
        "Together they are what Perforce calls Submit."
    )


def test_labels_use_lore_words_only(qapp) -> None:
    dlg = build()
    texts = [
        w.text()
        for kind in (QtWidgets.QPushButton, QtWidgets.QLabel, QtWidgets.QCheckBox)
        for w in dlg.findChildren(kind)
    ]
    texts += [g.text(0) for g in dlg._group_items] + [dlg.windowTitle()]
    for text in texts:
        for banned in ("submit", "changelist", "check in", "get latest"):
            assert banned not in text.lower(), text
    assert {"Commit", "Cancel", "Push after commit", "Keep files locked"} <= set(texts)


def test_successful_commit_closes_and_returns_result(qapp) -> None:
    session = FakeSession(commit_result=outcome(output="done"))
    dlg = build(session)
    dlg.summary_edit.setText("  Fix rig  ")
    dlg.description_edit.setPlainText("Longer text\n")
    dlg.commit_button.click()
    assert session.commit_calls == [
        (["s1.hip", "s2.hip", "o.bgeo"], "Fix rig", "Longer text", True, False)
    ]
    assert dlg.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert dlg.outcome.pushed


def test_commit_without_push_closes(qapp) -> None:
    session = FakeSession(commit_result=outcome(pushed=False))
    dlg = build(session)
    dlg.push_box.setChecked(False)
    dlg.summary_edit.setText("x")
    dlg.commit_button.click()
    assert session.commit_calls[0][3] is False
    assert dlg.result() == QtWidgets.QDialog.DialogCode.Accepted


def test_push_failure_enters_retry_state_and_retry_succeeds(qapp) -> None:
    failed = outcome(pushed=False, push_error="[Error] Server unreachable.\nextra")
    session = FakeSession(commit_result=failed)
    dlg = build(session)
    dlg.summary_edit.setText("x")
    dlg.keep_box.setChecked(True)
    dlg.commit_button.click()

    assert dlg.result() != QtWidgets.QDialog.DialogCode.Accepted
    assert not dlg.banner.isHidden()
    assert dlg.banner.text() == (
        "Committed locally. Push failed: [Error] Server unreachable. "
        "Nothing has reached the server yet."
    )
    assert dlg.commit_button.isHidden()
    assert not dlg.retry_button.isHidden()
    assert dlg.retry_button.text() == "Retry push"
    assert not dlg.tree.isEnabled() and not dlg.summary_edit.isEnabled()
    assert not dlg.keep_box.isEnabled() and dlg.keep_box.isChecked()
    assert dlg.cancel_button.text() == "Close"

    dlg.retry_button.click()
    assert session.retry_calls == [()]
    assert not dlg.keep_box.isEnabled()
    assert dlg.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert dlg.outcome.pushed


def test_diverged_push_failure_text_points_to_sync(qapp) -> None:
    failed = outcome(pushed=False, diverged=True, push_error="[Error] Branch has diverged")
    dlg = build(FakeSession(commit_result=failed))
    dlg.summary_edit.setText("x")
    dlg.commit_button.click()
    text = dlg.banner.text()
    assert "behind remote" in text and "lore sync" in text
    assert "Push failed" not in text
    assert not dlg.retry_button.isHidden()


def test_retry_failure_stays_in_retry_state(qapp) -> None:
    session = FakeSession(commit_result=outcome(pushed=False, push_error="first"))
    session.retry_results = [outcome(pushed=False, push_error="second")]
    dlg = build(session)
    dlg.summary_edit.setText("x")
    dlg.commit_button.click()
    dlg.retry_button.click()
    assert "Push failed: second." in dlg.banner.text()
    assert dlg.result() != QtWidgets.QDialog.DialogCode.Accepted
    assert dlg.outcome.push_error == "second"


def test_commit_error_shows_error_and_stays_open(qapp, monkeypatch) -> None:
    shown = []
    monkeypatch.setattr(dialogs, "show_error", lambda exc, ctx="": shown.append((exc, ctx)))
    session = FakeSession()
    session.commit_error = CommitBlocked(["b.hip"])
    dlg = build(session)
    dlg.summary_edit.setText("x")
    dlg.commit_button.click()
    assert len(shown) == 1
    assert shown[0][0] is session.commit_error
    assert shown[0][1] == "Commit failed"
    assert dlg.outcome is None
    assert dlg.isEnabled()
    assert dlg.banner.isHidden()


def test_commit_error_in_lore_is_shown(qapp, monkeypatch) -> None:
    shown = []
    monkeypatch.setattr(dialogs, "show_error", lambda exc, ctx="": shown.append(exc))
    session = FakeSession()
    session.commit_error = LoreCommandError("commit failed", ["lore", "commit"], 1, "raw")
    dlg = build(session)
    dlg.summary_edit.setText("x")
    dlg.commit_button.click()
    assert shown == [session.commit_error]


def test_show_commit_returns_local_result_when_closed_after_push_failure(
    qapp, monkeypatch
) -> None:
    session = FakeSession(commit_result=outcome(pushed=False, push_error="nope"))

    def fake_exec(self) -> int:
        self.summary_edit.setText("x")
        self.commit_button.click()
        self.cancel_button.click()
        return 0

    monkeypatch.setattr(dialogs._CommitDialog, "exec", fake_exec)
    result = dialogs.show_commit(session)
    assert result is not None and not result.pushed


def test_show_commit_cancel_returns_none(qapp, monkeypatch) -> None:
    monkeypatch.setattr(dialogs._CommitDialog, "exec", lambda self: self.cancel_button.click() or 0)
    assert dialogs.show_commit(FakeSession()) is None


def test_show_commit_takes_option_defaults_from_the_prefs_argument(qapp, monkeypatch) -> None:
    pushes = []
    monkeypatch.setattr(
        dialogs._CommitDialog, "exec", lambda self: pushes.append(self.push_box.isChecked()) or 0
    )
    dialogs.show_commit(FakeSession(), prefs.Prefs(push_after_commit=False))
    dialogs.show_commit(FakeSession())
    assert pushes == [False, True]


def test_show_commit_with_no_changes_says_so(qapp, monkeypatch, shown) -> None:
    session = FakeSession(status=make_status())
    assert dialogs.show_commit(session) is None
    assert shown == [("Lore - Commit", "There are no changes to commit.")]


def test_show_commit_reports_read_errors(qapp, monkeypatch) -> None:
    shown = []
    monkeypatch.setattr(dialogs, "show_error", lambda exc, ctx="": shown.append(ctx))
    session = FakeSession()
    session.status = lambda: (_ for _ in ()).throw(
        LoreCommandError("status failed", ["lore", "status"], 1, "raw")
    )
    assert dialogs.show_commit(session) is None
    assert shown == ["Could not read changes"]


def test_dialog_parents_to_host_window(qapp, monkeypatch) -> None:
    host = QtWidgets.QWidget()
    ui.set_parent_provider(lambda: host)
    parents = []

    def fake_exec(self) -> int:
        parents.append(self.parent())
        return 0

    monkeypatch.setattr(dialogs._CommitDialog, "exec", fake_exec)
    dialogs.show_commit(FakeSession())
    assert parents == [host]


def test_thousands_of_rows_build_and_toggle(qapp) -> None:
    paths = [f"cache/f{i:05d}.bgeo" for i in range(2000)]
    session = FakeSession(
        status=make_status(untracked=paths), states=[state(paths[0], LockState.NOT_LOCKED)]
    )
    dlg = build(session)
    other = group(dlg, "Other changes")
    other.setCheckState(0, CHECKED)
    assert len(dlg.checked_paths()) == 2000
    assert dlg.count_label.text() == "2000 files"


def info_values(session) -> dict[str, str]:
    data = dialogs._load_info(session)
    dlg = dialogs._InfoDialog(session, data, None)
    return {name: label.text() for name, label in dlg.values.items()}


def test_info_rows(qapp) -> None:
    values = info_values(FakeSession())
    assert values == {
        "Working copy": str(Path("/work/repo")),
        "Branch": "main",
        "Revision": "7",
        "Remote state": "In sync with remote",
        "Remote URL": "lore://host/repo",
        "Identity": "Ben",
        "Lore version": "0.9.0",
    }


def test_info_not_signed_in_hint(qapp) -> None:
    values = info_values(FakeSession(fail_auth=True))
    assert values["Identity"] == "Not signed in. Run in a terminal: lore login lore://host/repo"


def test_info_not_signed_in_without_remote_url(qapp) -> None:
    session = FakeSession(fail_auth=True)
    session.workspace.remote_url = None
    assert info_values(session)["Identity"] == "Not signed in. Run in a terminal: lore login"


def test_show_info_executes_dialog(qapp, monkeypatch) -> None:
    ran = []
    monkeypatch.setattr(dialogs._InfoDialog, "exec", lambda self: ran.append(self.values) or 0)
    dialogs.show_info(FakeSession())
    assert len(ran) == 1


def test_show_info_reports_status_failure(qapp, monkeypatch) -> None:
    shown = []
    monkeypatch.setattr(dialogs, "show_error", lambda exc, ctx="": shown.append(ctx))
    session = FakeSession()
    session.status = lambda: (_ for _ in ()).throw(
        LoreCommandError("status failed", ["lore", "status"], 1, "raw")
    )
    dialogs.show_info(session)
    assert shown == ["Could not read Lore info"]


def test_dialog_window_titles_start_with_lore(qapp) -> None:
    dlg = build()
    assert dlg.windowTitle() == "Lore - Commit"
    info = dialogs._InfoDialog(FakeSession(), dialogs._load_info(FakeSession()), None)
    assert info.windowTitle() == "Lore - Info"


def test_commit_and_push_shows_the_pushed_message(qapp, shown) -> None:
    dlg = build(FakeSession(commit_result=outcome(revision=3, pushed_revision=3, server_revision=3)))
    dlg.summary_edit.setText("x")
    dlg.commit_button.click()
    assert shown == [
        (
            "Lore - Commit Complete",
            "Committed revision 3 and pushed it to main.\nThe server is at revision 3.",
        )
    ]
    assert dlg.result() == QtWidgets.QDialog.DialogCode.Accepted


def test_commit_without_push_shows_the_local_message(qapp, shown) -> None:
    result = outcome(pushed=False, pushed_revision=None, branch=None, revision=5, server_revision=4)
    dlg = build(FakeSession(commit_result=result))
    dlg.push_box.setChecked(False)
    dlg.summary_edit.setText("x")
    dlg.commit_button.click()
    assert shown == [
        (
            "Lore - Commit Complete",
            (
                "Committed revision 5 locally. It has not been pushed, so nothing has reached "
                "the server yet.\nThe server is at revision 4."
            ),
        )
    ]
    assert dlg.result() == QtWidgets.QDialog.DialogCode.Accepted


def test_failed_push_shows_no_complete_message_until_the_retry_succeeds(qapp, shown) -> None:
    session = FakeSession(commit_result=outcome(pushed=False, push_error="nope", revision=6))
    session.retry_results = [outcome(revision=None, pushed_revision=6, server_revision=6)]
    dlg = build(session)
    dlg.summary_edit.setText("x")
    dlg.commit_button.click()
    assert shown == []
    dlg.retry_button.click()
    assert shown == [
        (
            "Lore - Commit Complete",
            "Committed revision 6 and pushed it to main.\nThe server is at revision 6.",
        )
    ]
