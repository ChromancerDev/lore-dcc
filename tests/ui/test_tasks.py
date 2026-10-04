"""run_blocking and run_in_background: worker thread, event pumping, busy dialog, exceptions."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6")

from lore_dcc.ui import tasks
from PySide6 import QtCore, QtWidgets


def test_returns_result_and_passes_arguments(qapp) -> None:
    assert tasks.run_blocking(lambda a, b=0: a + b, 2, b=3, title="Add") == 5


def test_runs_on_a_worker_thread(qapp) -> None:
    thread_ids = []
    tasks.run_blocking(lambda: thread_ids.append(threading.get_ident()))
    assert thread_ids and thread_ids[0] != threading.get_ident()


def test_reraises_in_caller(qapp) -> None:
    def boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        tasks.run_blocking(boom)


def test_caller_thread_keeps_pumping_events(qapp) -> None:
    ticks = []
    timer = QtCore.QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(20)
    tasks.run_blocking(time.sleep, 0.3)
    timer.stop()
    assert len(ticks) >= 3


def test_fast_call_shows_no_busy_dialog(qapp, monkeypatch) -> None:
    created = []
    original = tasks._BusyDialog

    def recording(*args, **kwargs):
        created.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(tasks, "_BusyDialog", recording)
    tasks.run_blocking(lambda: 1)
    assert created == []


def test_slow_call_shows_modal_busy_dialog_without_cancel(qapp) -> None:
    seen = {}

    def probe() -> None:
        for widget in QtWidgets.QApplication.topLevelWidgets():
            if isinstance(widget, tasks._BusyDialog) and widget.isVisible():
                seen["modal"] = widget.windowModality()
                seen["cancel"] = widget.findChild(QtWidgets.QPushButton)
                seen["title"] = widget.windowTitle()
                seen["range"] = (widget.minimum(), widget.maximum())

    QtCore.QTimer.singleShot(550, probe)
    tasks.run_blocking(time.sleep, 0.9, title="Working")
    assert seen["modal"] == QtCore.Qt.WindowModality.ApplicationModal
    assert seen["title"] == "Lore - Working"
    assert seen["range"] == (0, 0)
    assert seen["cancel"] is None or not seen["cancel"].isVisible()
    assert not any(
        isinstance(w, tasks._BusyDialog) and w.isVisible() for w in qapp.topLevelWidgets()
    )


def test_runs_inline_without_application(monkeypatch) -> None:
    monkeypatch.setattr(QtWidgets.QApplication, "instance", staticmethod(lambda: None))
    seen = []
    assert tasks.run_blocking(lambda: seen.append(threading.get_ident()) or 7) == 7
    assert seen == [threading.get_ident()]


def test_event_loop_excludes_user_input(qapp, monkeypatch) -> None:
    flags = []
    real = QtCore.QEventLoop

    class RecordingLoop(real):
        def exec(self, *args):
            flags.append(args)
            return super().exec(*args)

    monkeypatch.setattr(QtCore, "QEventLoop", RecordingLoop)
    assert tasks.run_blocking(lambda: 1) == 1
    assert flags == [(real.ProcessEventsFlag.ExcludeUserInputEvents,)]


def _wait_for(qapp, done: list) -> None:
    deadline = time.monotonic() + 5
    while not done and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def test_background_returns_at_once_and_delivers_on_the_ui_thread(qapp) -> None:
    got = []
    started = time.monotonic()
    tasks.run_in_background(
        lambda a: (time.sleep(0.3), a * 2, threading.get_ident())[1:],
        4,
        on_done=lambda result, error: got.append((result, error, threading.get_ident())),
    )
    assert time.monotonic() - started < 0.2
    assert got == []
    _wait_for(qapp, got)
    (result, error, delivered_on), = got
    assert (result[0], error) == (8, None)
    assert result[1] != threading.get_ident()
    assert delivered_on == threading.get_ident()


def test_background_delivers_an_error_and_releases_the_notifier(qapp) -> None:
    got = []

    def boom() -> None:
        raise ValueError("nope")

    tasks.run_in_background(boom, on_done=lambda result, error: got.append((result, error)))
    assert len(tasks._pending) == 1
    _wait_for(qapp, got)
    assert got[0][0] is None and isinstance(got[0][1], ValueError)
    assert tasks._pending == set()


def test_background_runs_inline_without_application(monkeypatch) -> None:
    monkeypatch.setattr(QtWidgets.QApplication, "instance", staticmethod(lambda: None))
    got = []
    tasks.run_in_background(lambda: 3, on_done=lambda result, error: got.append((result, error)))
    tasks.run_in_background(
        lambda: 1 / 0, on_done=lambda result, error: got.append((result, type(error)))
    )
    assert got == [(3, None), (None, ZeroDivisionError)]


def test_background_waits_for_a_running_blocking_call(qapp) -> None:
    got = []
    tasks.run_in_background(lambda: 1, on_done=lambda result, error: got.append(result))
    tasks.run_blocking(time.sleep, 0.3)
    assert got == []
    _wait_for(qapp, got)
    assert got == [1]
