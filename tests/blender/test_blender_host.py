"""The Blender runner's threading: one lock for every Lore call, and a clean stop."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from lore_dcc.blender import api, flow, host
from lore_dcc.blender.flow import Run
from lore_dcc.session import WriteResult


def _wait(event: threading.Event) -> None:
    assert event.wait(5), "timed out"


def test_script_api_waits_for_a_commit_running_on_the_worker(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / ".lore").mkdir(parents=True)
    order: list[str] = []
    started, release = threading.Event(), threading.Event()

    def commit() -> None:
        order.append("commit read")
        started.set()
        _wait(release)
        order.append("commit done")

    session = SimpleNamespace(
        workspace=SimpleNamespace(root=repo.resolve()),
        finish_write=lambda paths: order.append("api stage") or WriteResult((), (), ()),
    )
    monkeypatch.setattr(host, "session_for", lambda path: session)
    runner = host.BlenderRunner()

    def action():
        yield Run(commit)

    flow.start(action(), runner)
    _wait(started)
    staged = threading.Thread(target=api.stage, args=([str(repo / "a.png")],))
    staged.start()
    staged.join(0.2)
    assert order == ["commit read"]
    release.set()
    staged.join(5)
    assert order == ["commit read", "commit done", "api stage"]
    runner.stop()


def test_stop_drops_queued_calls_and_closes_their_flows() -> None:
    runner = host.BlenderRunner()
    started, release = threading.Event(), threading.Event()
    ran: list[str] = []
    closed: list[str] = []

    def blocking() -> None:
        started.set()
        _wait(release)
        ran.append("first")

    def action(fn, name):
        try:
            yield Run(fn)
            yield Run(fn)
        finally:
            closed.append(name)

    flow.start(action(blocking, "a"), runner)
    _wait(started)
    flow.start(action(lambda: ran.append("second"), "b"), runner)
    runner.stop()
    release.set()
    runner._worker.join(5)
    assert not runner._worker.is_alive()
    assert ran == ["first"]
    assert "b" in closed
    assert runner.tick() == host._IDLE_INTERVAL
    assert runner.current is None


def test_shutdown_forgets_the_runner_so_register_starts_clean(monkeypatch) -> None:
    monkeypatch.setattr(host, "has_ui", lambda: True)
    first = host.runner()
    assert isinstance(first, host.BlenderRunner)
    host.shutdown()
    assert first._stopped
    second = host.runner()
    assert second is not first and not second._stopped
    host.shutdown()


def test_addon_unregister_shuts_the_runner_down(monkeypatch) -> None:
    from lore_dcc.blender import addon

    stopped: list[bool] = []
    monkeypatch.setattr(host, "shutdown", lambda: stopped.append(True))
    addon.register("bl_ext.user_default.lore_dcc")
    addon.unregister()
    assert stopped == [True]
