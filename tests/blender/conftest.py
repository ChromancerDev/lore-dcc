"""Give the Blender tests a stand-in `bpy` module outside Blender."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest


class _Struct:
    """Base for the fake bpy.types classes."""


class _MenuType:
    """A built-in menu that add-ons append draw functions to."""

    def __init__(self) -> None:
        self.draws: list = []

    def append(self, draw) -> None:
        self.draws.append(draw)

    def remove(self, draw) -> None:
        self.draws.remove(draw)


def _prop(kind: str):
    def make(**kwargs):
        return (kind, kwargs)

    return make


def _abspath(path: str, library=None) -> str:
    if path.startswith("//"):
        base = os.path.dirname(_bpy.data.filepath) if _bpy.data.filepath else os.getcwd()
        return os.path.join(base, path[2:])
    return path


def _make_bpy() -> types.ModuleType:
    bpy = types.ModuleType("bpy")
    bpy.types = types.SimpleNamespace(
        Operator=type("Operator", (_Struct,), {}),
        Menu=type("Menu", (_Struct,), {}),
        Panel=type("Panel", (_Struct,), {}),
        PropertyGroup=type("PropertyGroup", (_Struct,), {}),
        UIList=type("UIList", (_Struct,), {}),
        AddonPreferences=type("AddonPreferences", (_Struct,), {}),
        Scene=type("Scene", (), {}),
        WindowManager=type("WindowManager", (), {}),
        TOPBAR_MT_editor_menus=_MenuType(),
        UI_MT_button_context_menu=_MenuType(),
        OUTLINER_MT_context_menu=_MenuType(),
        OUTLINER_MT_id_data=_MenuType(),
    )
    bpy.props = types.SimpleNamespace(
        **{
            name: _prop(name)
            for name in (
                "StringProperty",
                "BoolProperty",
                "IntProperty",
                "CollectionProperty",
                "PointerProperty",
            )
        }
    )
    handlers = types.ModuleType("bpy.app.handlers")
    for name in ("save_pre", "save_post", "load_post", "render_init", "render_complete"):
        setattr(handlers, name, [])
    handlers.persistent = lambda fn: fn
    timers = types.SimpleNamespace(registered=[])
    timers.register = lambda fn, **kw: timers.registered.append(fn)
    timers.is_registered = lambda fn: fn in timers.registered
    timers.unregister = lambda fn: timers.registered.remove(fn)
    app = types.ModuleType("bpy.app")
    app.handlers = handlers
    app.timers = timers
    app.background = True
    app.version_string = "5.2.2 LTS"
    bpy.app = app
    registered: list = []
    bpy.utils = types.SimpleNamespace(
        registered=registered,
        register_class=registered.append,
        unregister_class=registered.remove,
        user_resource=lambda kind: str(Path(os.environ["LORE_DCC_TEST_CONFIG"])),
        resource_path=lambda kind: "",
    )
    bpy.data = types.SimpleNamespace(filepath="", is_dirty=False)
    bpy.path = types.SimpleNamespace(abspath=_abspath)
    bpy.context = types.SimpleNamespace(scene=None, window_manager=None)
    bpy.ops = types.SimpleNamespace()
    sys.modules["bpy"] = bpy
    sys.modules["bpy.app"] = app
    sys.modules["bpy.app.handlers"] = handlers
    return bpy


try:
    import bpy as _bpy
except ImportError:
    _bpy = _make_bpy()


@pytest.fixture(autouse=True)
def _blender_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point prefs at a temp folder and reset module state between tests."""
    monkeypatch.setenv("LORE_DCC_TEST_CONFIG", str(tmp_path / "config"))
    from lore_dcc.blender import hooks, host

    monkeypatch.setattr(host, "_runner", None)
    monkeypatch.setattr(hooks, "_unknown_warned", False)
    monkeypatch.setattr(hooks, "_keep_unlocked", set())
    monkeypatch.setattr(hooks, "_other_warned", set())
    monkeypatch.setattr(_bpy.data, "filepath", "")
    monkeypatch.setattr(_bpy.data, "is_dirty", False)
    yield
