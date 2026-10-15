"""A refused lock in a ROP's pre-render stops the render in the UI too."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import hou
import pytest
from lore_dcc.errors import LockNotAcquired
from lore_dcc.houdini import api, host, outputs


@pytest.fixture
def refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def refuse(paths):
        raise LockNotAcquired(["geo/out.bgeo.sc"], "geo/out.bgeo.sc locked by sam")

    session = SimpleNamespace(prepare_write=refuse, finish_write=refuse)
    errors: list[BaseException] = []
    monkeypatch.setattr(hou, "hipFile", SimpleNamespace(path=lambda: str(tmp_path / "a.hip")), raising=False)
    monkeypatch.setattr(host, "load_prefs", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(host, "session_for", lambda path: session)
    monkeypatch.setattr(host, "run", lambda fn, *args, title="": fn(*args))
    monkeypatch.setattr(host, "has_ui", lambda: True)
    monkeypatch.setattr(host, "error", lambda exc, context="": errors.append(exc))
    monkeypatch.setattr(outputs, "outputs_for_node", lambda node, s: [tmp_path / "geo/out.bgeo.sc"])
    return SimpleNamespace(node=SimpleNamespace(path=lambda: "/out/geo"), errors=errors)


def test_pre_render_raises_after_the_dialog_in_the_ui(refused) -> None:
    with pytest.raises(LockNotAcquired):
        api.pre_render(refused.node)
    assert len(refused.errors) == 1


def test_post_render_only_shows_the_dialog_in_the_ui(refused) -> None:
    assert api.post_render(refused.node) is None
    assert len(refused.errors) == 1
