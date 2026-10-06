"""Message boxes: error mapping, details text, custom buttons."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from lore_dcc import ui
from lore_dcc.errors import (
    LockNotAcquired,
    LoreCommandError,
    LoreNotFound,
    LoreOutputError,
    NotAWorkingCopy,
)
from lore_dcc.locks import LockState
from lore_dcc.session import CommitBlocked, FileState
from lore_dcc.ui import messages
from PySide6 import QtWidgets


def test_command_error_puts_output_in_details(qapp) -> None:
    err = LoreCommandError("lore push failed", ["lore", "push"], 255, "[Error] boom\nmore")
    box = messages._error_box(err, "Push")
    assert "lore push failed" in box.text()
    assert box.text().startswith("Push")
    assert box.detailedText() == "[Error] boom\nmore"


def test_output_error_uses_raw(qapp) -> None:
    box = messages._error_box(LoreOutputError("bad line", "RAW TEXT"), "")
    assert box.detailedText() == "RAW TEXT"
    assert box.text() == "bad line"


def test_lock_not_acquired_uses_output(qapp) -> None:
    box = messages._error_box(LockNotAcquired(["a.hip"], "Lock failed for a.hip"), "")
    assert box.detailedText() == "Lock failed for a.hip"


def test_not_a_working_copy_text(qapp) -> None:
    box = messages._error_box(NotAWorkingCopy("Not a Lore working copy: C:/x"), "")
    assert box.text() == "Not a Lore working copy"


def test_lore_not_found_tells_user_to_install(qapp) -> None:
    text = messages._error_box(LoreNotFound("missing"), "").text()
    assert "Install Lore" in text
    assert "PATH" in text


def test_commit_blocked_lists_paths(qapp) -> None:
    states = [
        FileState("a.hip", True, True, True, LockState.LOCKED_BY_OTHER, "Bob"),
        FileState("b.hip", True, True, True, LockState.UNKNOWN, None),
    ]
    box = messages._error_box(CommitBlocked(states), "")
    assert box.detailedText() == "a.hip (locked by Bob)\nb.hip (lock state unknown)"


def test_plain_error_falls_back_to_message(qapp) -> None:
    assert messages._error_box(ValueError("bad"), "").text() == "bad"
    assert messages._error_box(ValueError(), "").text() == "ValueError"


def test_boxes_parent_to_host_window(qapp) -> None:
    host = QtWidgets.QWidget()
    ui.set_parent_provider(lambda: host)
    assert messages._error_box(ValueError("x"), "").parent() is host


def test_show_error_and_message_exec(qapp, monkeypatch) -> None:
    shown = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "exec", lambda self: shown.append(self.text()) or 0
    )
    messages.show_error(ValueError("bad"), "ctx")
    messages.show_message("Title", "Hello")
    assert shown == ["ctx\n\nbad", "Hello"]


def test_ask_returns_clicked_label(qapp, monkeypatch) -> None:
    def fake_exec(self) -> int:
        assert self.defaultButton().text() == "Stage it"
        next(b for b in self.buttons() if b.text() == "Not now").click()
        return 0

    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", fake_exec)
    assert messages.ask("Q", "Stage?", ["Stage it", "Not now"], default="Stage it") == "Not now"


def test_ask_returns_none_when_closed(qapp, monkeypatch) -> None:
    monkeypatch.setattr(QtWidgets.QMessageBox, "exec", lambda self: 0)
    assert messages.ask("Q", "Stage?", ["Yes", "No"]) is None


@pytest.mark.parametrize(
    ("given", "shown"),
    [
        ("Stage New File?", "Lore - Stage New File?"),
        ("Lore - Lock File?", "Lore - Lock File?"),
        ("Lore", "Lore - Info"),
        ("", "Lore - Info"),
    ],
)
def test_window_title_always_starts_with_the_lore_prefix(given: str, shown: str) -> None:
    assert ui.window_title(given) == shown


def test_every_box_title_starts_with_the_lore_prefix(qapp) -> None:
    titles = [
        messages._error_box(ValueError("x"), "").windowTitle(),
        messages._ask_box("Stage?", "t", ["Yes"], None).windowTitle(),
        messages._box(QtWidgets.QMessageBox.Icon.Information, "Info", "t").windowTitle(),
        messages._box(QtWidgets.QMessageBox.Icon.Information, "Lore", "t").windowTitle(),
    ]
    assert titles == ["Lore - Error", "Lore - Stage?", "Lore - Info", "Lore - Info"]


def test_busy_dialog_title_starts_with_the_lore_prefix(qapp) -> None:
    from lore_dcc.ui import tasks

    assert tasks._BusyDialog("Locking", None).windowTitle() == "Lore - Locking"
