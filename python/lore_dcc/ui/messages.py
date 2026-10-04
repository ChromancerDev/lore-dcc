"""Modal message boxes parented to the host window."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6 import QtWidgets

from lore_dcc.errors import (
    LockNotAcquired,
    LoreCommandError,
    LoreNotFound,
    LoreOutputError,
    NotAWorkingCopy,
)
from lore_dcc.session import CommitBlocked
from lore_dcc.ui import parent_window

_TITLE = "Lore"


def _box(
    icon: QtWidgets.QMessageBox.Icon, title: str, text: str, details: str = ""
) -> QtWidgets.QMessageBox:
    box = QtWidgets.QMessageBox(parent_window())
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    if details:
        box.setDetailedText(details)
    return box


def _ask_box(
    title: str, text: str, buttons: Sequence[str], default: str | None
) -> QtWidgets.QMessageBox:
    box = _box(QtWidgets.QMessageBox.Icon.Question, title, text)
    for label in buttons:
        button = box.addButton(label, QtWidgets.QMessageBox.ButtonRole.ActionRole)
        if label == default:
            box.setDefaultButton(button)
    return box


def ask(title: str, text: str, buttons: Sequence[str], default: str | None = None) -> str | None:
    """Show a question with custom button labels.

    Returns:
        The clicked label, or None when the dialog was closed.
    """
    box = _ask_box(title, text, buttons, default)
    box.exec()
    clicked = box.clickedButton()
    if clicked is None or clicked.text() not in buttons:
        return None
    return clicked.text()


def _error_box(error: BaseException, context: str) -> QtWidgets.QMessageBox:
    details = ""
    if isinstance(error, NotAWorkingCopy):
        message = "Not a Lore working copy"
        details = str(error)
    elif isinstance(error, LoreNotFound):
        message = "Lore was not found. Install Lore and put `lore` on your PATH."
    elif isinstance(error, (LoreCommandError, LockNotAcquired)):
        message = str(error)
        details = error.output
    elif isinstance(error, LoreOutputError):
        message = str(error)
        details = error.raw
    elif isinstance(error, CommitBlocked):
        message = str(error)
        details = "\n".join(error.paths)
    else:
        message = str(error) or type(error).__name__
    text = f"{context}\n\n{message}" if context else message
    return _box(QtWidgets.QMessageBox.Icon.Critical, _TITLE, text, details)


def show_error(error: BaseException, context: str = "") -> None:
    """Show an error. LoreCommandError, LoreOutputError and LockNotAcquired
    put Lore's raw output in the expandable details area."""
    _error_box(error, context).exec()


def show_message(title: str, text: str) -> None:
    """Show an information message with one OK button."""
    box = _box(QtWidgets.QMessageBox.Icon.Information, title, text)
    box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
    box.exec()
