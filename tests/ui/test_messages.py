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
from lore_dcc.session import CommitBlocked
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
    box = messages._error_box(CommitBlocked(["a.hip", "b.hip"]), "")
    assert box.detailedText() == "a.hip\nb.hip"


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
