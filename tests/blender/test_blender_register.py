"""Add-on registration, the Lore menu, the manifest and the bpy-free helpers."""

from __future__ import annotations

import importlib.util
import os
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import bpy
import pytest
from lore_dcc import __version__
from lore_dcc.blender import addon, api, commit_ui, hooks, host, operators, outputs, references

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def registered():
    addon.register("bl_ext.user_default.lore_dcc")
    yield bpy.utils.registered
    addon.unregister()


def test_register_adds_classes_menus_and_handlers_and_unregister_removes_them(registered) -> None:
    names = {cls.__name__ for cls in registered}
    assert {"LORE_MT_main", "LORE_OT_dialog", "LORE_OT_commit_dialog", "LORE_PT_outputs"} <= names
    prefs = next(c for c in registered if c.__name__ == "LORE_AddonPreferences")
    assert prefs.bl_idname == "bl_ext.user_default.lore_dcc"
    assert operators.draw_topbar in bpy.types.TOPBAR_MT_editor_menus.draws
    assert operators.draw_button_menu in bpy.types.UI_MT_button_context_menu.draws
    for name, handler in hooks.HANDLERS:
        assert handler in getattr(bpy.app.handlers, name)
    assert hasattr(bpy.types.Scene, "lore") and hasattr(bpy.types.WindowManager, "lore_commit")
    addon.unregister()
    assert bpy.utils.registered == []
    assert bpy.types.TOPBAR_MT_editor_menus.draws == []
    assert all(handler not in getattr(bpy.app.handlers, n) for n, handler in hooks.HANDLERS)
    assert not hasattr(bpy.types.Scene, "lore")
    addon.register("bl_ext.user_default.lore_dcc")


def test_operator_ids_and_labels() -> None:
    for cls in operators.CLASSES:
        idname = getattr(cls, "bl_idname", "")
        if issubclass(cls, bpy.types.Operator):
            assert idname.startswith("lore."), idname
            assert cls.__name__ == "LORE_OT_" + idname.split(".")[1]
            assert getattr(cls, "bl_description", cls.__doc__)


def _menu_labels(cls) -> list[str | None]:
    drawn: list[str | None] = []
    layout = SimpleNamespace(
        separator=lambda: drawn.append(None),
        operator=lambda idname, text="", icon="": drawn.append(text),
    )
    cls.draw(SimpleNamespace(layout=layout), SimpleNamespace())
    return drawn


def test_lore_menu_matches_the_houdini_menu() -> None:
    menu = ET.parse(ROOT / "houdini" / "MainMenuCommon.xml").getroot().find("menuBar/subMenu")
    assert menu is not None
    houdini = [
        child.findtext("label") if child.tag == "scriptItem" else None
        for child in menu
        if child.tag in ("scriptItem", "separatorItem")
    ]
    assert _menu_labels(operators.LORE_MT_main) == houdini


def test_menu_entries_run_the_matching_actions() -> None:
    expected = {
        "lore.info": "info", "lore.lock": "lock", "lore.discard": "discard",
        "lore.collect_outputs": "collect_outputs", "lore.sync_dependencies": "sync_dependencies",
        "lore.commit": "commit", "lore.toggle_enabled": "toggle_enabled",
        "lore.file_a_bug": "file_a_bug",
    }  # fmt: skip
    items = {item[0]: item[3].__name__ for item in operators.MENU_ITEMS if item}
    assert items == expected


def test_manifest_matches_the_package() -> None:
    manifest = tomllib.loads((ROOT / "blender" / "blender_manifest.toml").read_text("utf-8"))
    assert manifest["version"] == __version__
    assert (manifest["id"], manifest["type"]) == ("lore_dcc", "add-on")
    assert manifest["blender_version_min"] == "5.2.0"
    assert manifest["license"] == ["SPDX:MIT"]


def test_extension_shim_leaves_sys_path_to_blender() -> None:
    spec = importlib.util.spec_from_file_location("lore_dcc_shim", ROOT / "blender" / "__init__.py")
    assert spec is not None and spec.loader is not None
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)
    assert callable(shim.register) and callable(shim.unregister)
    assert "sys.path" not in (ROOT / "blender" / "__init__.py").read_text("utf-8")


def test_manifest_lists_the_wheel_the_release_builds() -> None:
    manifest = tomllib.loads((ROOT / "blender" / "blender_manifest.toml").read_text("utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert project["version"] == __version__
    assert manifest["wheels"] == [f"./wheels/lore_dcc-{__version__}-py3-none-any.whl"]


def test_preferences_read_and_write_the_json_file(registered) -> None:
    prefs = next(c for c in registered if c.__name__ == "LORE_AddonPreferences")
    kind, options = prefs.__annotations__["prompt_stage_after_save"]
    assert kind == "BoolProperty"
    options["set"](None, False)
    assert host.load_prefs().prompt_stage_after_save is False
    assert options["get"](None) is False


def test_button_file_reads_only_file_path_fields(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bpy.data, "filepath", str(tmp_path / "shot.blend"))
    image = SimpleNamespace(filepath="//tex/a.png", id_data=SimpleNamespace(library=None))
    prop = SimpleNamespace(type="STRING", subtype="FILE_PATH", identifier="filepath")
    context = SimpleNamespace(button_prop=prop, button_pointer=image)
    assert operators.button_file(context) == os.path.normpath(str(tmp_path / "tex" / "a.png"))
    context.button_prop = SimpleNamespace(type="STRING", subtype="NONE", identifier="filepath")
    assert operators.button_file(context) is None
    assert operators.button_file(SimpleNamespace()) is None


def test_udim_paths() -> None:
    assert references.udim_paths("D:/t/a.<UDIM>.png", [1001, 1012]) == [
        "D:/t/a.1001.png",
        "D:/t/a.1012.png",
    ]
    assert references.udim_paths("D:/t/a.<UVTILE>.png", [1012]) == ["D:/t/a.u2_v2.png"]
    assert references.udim_paths("D:/t/a.png", [1001]) == ["D:/t/a.png"]


def test_own_files_are_blenders_install(tmp_path) -> None:
    own = [os.path.normcase(str(tmp_path / "Blender 5.2" / "5.2"))]
    assert references.own_file(str(tmp_path / "Blender 5.2" / "5.2" / "brushes.blend"), own)
    assert not references.own_file(str(tmp_path / "Blender 5.2" / "5.2x" / "a.blend"), own)


def test_files_of_skips_packed_and_generated_data(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bpy.data, "filepath", str(tmp_path / "shot.blend"))
    image = SimpleNamespace(filepath="//a.png", packed_file=None, source="FILE", library=None)
    assert references.files_of(image) == [os.path.normpath(str(tmp_path / "a.png"))]
    assert references.files_of(SimpleNamespace(**{**vars(image), "packed_file": object()})) == []
    assert references.files_of(SimpleNamespace(**{**vars(image), "source": "GENERATED"})) == []
    assert references.files_of(SimpleNamespace(filepath="<builtin>", library=None)) == []


def test_frames_and_cap() -> None:
    assert outputs.frames(1, 5, 2) == ([1, 3, 5], 3)
    assert outputs.frames(5, 1, 1) == ([], 0)
    numbers, total = outputs.frames(1, outputs.MAX_FRAMES + 10, 1)
    assert (len(numbers), total) == (outputs.MAX_FRAMES, outputs.MAX_FRAMES + 10)


def test_files_in_and_inside(tmp_path) -> None:
    (tmp_path / "out" / "sub").mkdir(parents=True)
    for name in ("a.png", "b.txt", "sub/c.png"):
        (tmp_path / "out" / name).write_text("x")
    folder = str(tmp_path / "out")
    assert [Path(p).name for p in outputs.files_in(folder, recursive=False)] == ["a.png", "b.txt"]
    images = outputs.files_in(folder, recursive=True, suffixes=outputs.IMAGE_SUFFIXES)
    assert sorted(Path(p).name for p in images) == ["a.png", "c.png"]
    assert outputs.files_in(str(tmp_path / "missing"), recursive=True) == []
    found = outputs.inside([str(tmp_path / "out" / "a.png"), "", "Z:/elsewhere/a.png"], tmp_path)
    assert found == [(tmp_path / "out" / "a.png").resolve()]


def test_render_paths_over_the_frame_range(tmp_path) -> None:
    render = SimpleNamespace(
        filepath="//render/f_",
        is_movie_format=False,
        frame_path=lambda frame: str(tmp_path / "render" / f"f_{frame:04d}.png"),
    )
    scene = SimpleNamespace(render=render, frame_start=1, frame_end=3, frame_step=1)
    assert [Path(p).name for p in outputs.render_paths(scene)] == [
        "f_0001.png",
        "f_0002.png",
        "f_0003.png",
    ]
    render.is_movie_format = True
    assert len(outputs.render_paths(scene)) == 1
    render.filepath = " "
    assert outputs.render_paths(scene) == []


def test_api_groups_paths_by_working_copy_and_ignores_outside(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    (repo / ".lore").mkdir(parents=True)
    calls: list[tuple[str, list[Path]]] = []
    session = SimpleNamespace(
        workspace=SimpleNamespace(root=repo.resolve()),
        prepare_write=lambda paths: calls.append(("prepare", paths)) or ("a.png",),
    )
    monkeypatch.setattr(host, "session_for", lambda path: session)
    locked = api.lock([str(repo / "a.png"), str(tmp_path / "outside.png")])
    assert locked == ("a.png",)
    assert calls == [("prepare", [(repo / "a.png").resolve()])]
    assert api.lock(str(tmp_path / "outside.png")) == ()


def test_api_raises_after_the_popup(monkeypatch, tmp_path) -> None:
    from lore_dcc.errors import LockNotAcquired

    repo = tmp_path / "repo"
    (repo / ".lore").mkdir(parents=True)

    def refuse(paths):
        raise LockNotAcquired(["a.png"], "a.png locked by sam")

    session = SimpleNamespace(workspace=SimpleNamespace(root=repo.resolve()), prepare_write=refuse)
    monkeypatch.setattr(host, "session_for", lambda path: session)
    monkeypatch.setattr(host, "has_ui", lambda: True)
    shown: list[object] = []
    monkeypatch.setattr(host, "notify", shown.append)
    with pytest.raises(LockNotAcquired):
        api.lock(str(repo / "a.png"))
    assert len(shown) == 1


def test_commit_rows_header_toggles_its_group() -> None:
    def row(group, header=False, checked=False, enabled=True):
        return SimpleNamespace(
            group=group, is_header=header, checked=checked, enabled=enabled, path=group
        )

    rows = [
        row("Other", header=True),
        row("Other"),
        row("Other", enabled=False),
        row("Mine", header=True),
        row("Mine", checked=True),
    ]
    context = SimpleNamespace(window_manager=SimpleNamespace(lore_commit=SimpleNamespace(rows=rows)))
    rows[0].checked = True
    commit_ui._row_checked(rows[0], context)
    assert [r.checked for r in rows[:3]] == [True, True, False]
    rows[1].checked = False
    commit_ui._row_checked(rows[1], context)
    assert rows[0].checked is False
    assert commit_ui.checked_paths(rows) == ["Mine"]


def test_refused_pre_render_raises_after_the_popup_in_the_ui(monkeypatch, tmp_path) -> None:
    from lore_dcc.errors import LockNotAcquired

    repo = tmp_path / "repo"
    (repo / ".lore").mkdir(parents=True)
    monkeypatch.setattr(bpy.data, "filepath", str(repo / "shot.blend"))

    def refuse(paths):
        raise LockNotAcquired(["render/f_0001.png"], "render/f_0001.png locked by sam")

    session = SimpleNamespace(
        workspace=SimpleNamespace(root=repo.resolve()), prepare_write=refuse, finish_write=refuse
    )
    monkeypatch.setattr(host, "session_for", lambda path: session)
    monkeypatch.setattr(host, "has_ui", lambda: True)
    monkeypatch.setattr(outputs, "render_outputs", lambda scene, root: [repo / "render/f.png"])
    shown: list[object] = []
    monkeypatch.setattr(host, "notify", shown.append)
    scene = SimpleNamespace(name="Scene")
    with pytest.raises(LockNotAcquired):
        api.pre_render(scene)
    assert len(shown) == 1
    assert api.post_render(scene) is None
    assert len(shown) == 2
