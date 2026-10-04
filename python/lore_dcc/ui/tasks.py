"""Run Lore calls off the UI thread while the caller waits."""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from lore_dcc.ui import parent_window, window_title

_SHOW_AFTER_MS = 300


class _BusyDialog(QtWidgets.QProgressDialog):
    """Modal busy indicator with no cancel: Lore calls cannot be cancelled."""

    def __init__(self, title: str, parent: QtWidgets.QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle(window_title(title))
        self.setLabelText(f"{title}...")
        self.setRange(0, 0)
        self.setCancelButton(None)
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowCloseButtonHint, False)

    def reject(self) -> None:
        """Ignore Escape: only the finished call may dismiss this dialog."""

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Ignore user close requests."""
        event.ignore()


class _Notifier(QtCore.QObject):
    finished = QtCore.Signal()


def run_blocking[T](
    fn: Callable[..., T], *args: object, title: str = "Working", **kwargs: object
) -> T:
    """Run `fn(*args, **kwargs)` on a worker thread and return its result.

    The calling (UI) thread keeps processing Qt events, so the host never
    freezes. A modal progress dialog titled `title` appears if the call takes
    longer than 300 ms. User input is blocked while it runs, so a click or a
    shortcut such as Ctrl+S cannot re-enter a save hook. Exceptions raised by
    `fn` are re-raised in the caller.
    `fn` must not touch DCC APIs. Without a QApplication, `fn` runs inline.
    """
    app = QtWidgets.QApplication.instance()
    if not isinstance(app, QtWidgets.QApplication):
        return fn(*args, **kwargs)

    outcome: dict[str, object] = {}
    notifier = _Notifier()
    loop = QtCore.QEventLoop()
    notifier.finished.connect(loop.quit, QtCore.Qt.ConnectionType.QueuedConnection)

    def work() -> None:
        try:
            outcome["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            outcome["error"] = exc
        finally:
            notifier.finished.emit()

    busy: list[_BusyDialog] = []

    def show_busy() -> None:
        dialog = _BusyDialog(title, parent_window())
        busy.append(dialog)
        dialog.show()

    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(show_busy)
    thread = threading.Thread(target=work, name="lore-dcc-task", daemon=True)
    timer.start(_SHOW_AFTER_MS)
    thread.start()
    try:
        loop.exec(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    finally:
        timer.stop()
        for dialog in busy:
            dialog.hide()
            dialog.deleteLater()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["value"]  # type: ignore[return-value]
