"""Image sequences and compositor File Output names, checked against what Blender 5.2 wrote."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import bpy
from lore_dcc.blender import outputs, references


def _fmt(media: str = "IMAGE", file_format: str = "PNG") -> SimpleNamespace:
    return SimpleNamespace(media_type=media, file_format=file_format)


def _item(name: str, override: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, override_node_format=override is not None, format=_fmt(file_format=override or "")
    )


def _file_output(directory: str, file_name: str, items, *, media="IMAGE", fmt="PNG", mute=False):
    return SimpleNamespace(
        type="OUTPUT_FILE", name="File Output", mute=mute, directory=directory,
        file_name=file_name, format=_fmt(media, fmt), use_file_extension=True,
        file_output_items=items,
    )  # fmt: skip


def _scene(nodes, start=3, end=4) -> SimpleNamespace:
    return SimpleNamespace(
        compositing_node_group=SimpleNamespace(nodes=nodes),
        frame_start=start, frame_end=end, frame_step=1,
    )  # fmt: skip


def _rel(paths, root: Path) -> list[str]:
    return sorted(Path(p).relative_to(root).as_posix() for p in paths)


def test_frame_names_follow_blender() -> None:
    assert outputs.frame_name("pass_alpha", 3) == "pass_alpha0003"
    assert outputs.frame_name("p_###_c", 12) == "p_012_c"
    assert outputs.frame_name("a#_b##", 7) == "a#_b07"


def test_file_output_slots_with_subfolders_and_format_overrides(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bpy.data, "filepath", "")
    node = _file_output(
        str(tmp_path / "a"), "pass_", [_item("beauty/img"), _item("alpha", override="JPEG")]
    )
    paths = outputs.file_output_paths(_scene([node]))
    assert _rel(paths, tmp_path) == [
        "a/pass_alpha0003.jpg",
        "a/pass_alpha0004.jpg",
        "a/pass_beauty/img0003.png",
        "a/pass_beauty/img0004.png",
    ]


def test_multilayer_and_blend_name_and_groups(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bpy.data, "filepath", str(tmp_path / "shot_v3.blend"))
    multilayer = _file_output(
        str(tmp_path / "m"), "ml_", [_item("a"), _item("b")], media="MULTI_LAYER_IMAGE"
    )
    named = _file_output(str(tmp_path / "b"), "{blend_name}/", [_item("c")], fmt="TIFF")
    muted = _file_output(str(tmp_path / "x"), "x_", [_item("c")], mute=True)
    inner = SimpleNamespace(nodes=[named])
    group = SimpleNamespace(type="GROUP", mute=False, node_tree=inner)
    loop = SimpleNamespace(type="GROUP", mute=False, node_tree=inner)
    paths = outputs.file_output_paths(_scene([multilayer, group, loop, muted], 12, 12))
    assert _rel(paths, tmp_path) == ["b/shot_v3/c0012.tif", "m/ml_0012.exr"]


def test_unrelated_files_in_an_output_folder_are_never_outputs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bpy.data, "filepath", "")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "reference.png").write_text("x")
    (tmp_path / "a" / "pass_alpha0003.png").write_text("x")
    render = SimpleNamespace(filepath=" ")
    scene = _scene([_file_output(str(tmp_path / "a"), "pass_", [_item("alpha")])])
    scene.render = render
    scene.objects = []
    scene.rigidbody_world = None
    found = outputs.scene_outputs(scene, tmp_path)
    assert [p.name for p in found] == ["pass_alpha0003.png", "pass_alpha0004.png"]


def test_path_variables_and_video_are_noted_not_guessed(tmp_path) -> None:
    notes: list[str] = []
    nodes = [
        _file_output(str(tmp_path), "{fps}_", [_item("a")]),
        _file_output(str(tmp_path), "v_", [_item("a")], media="VIDEO", fmt="FFMPEG"),
    ]
    assert outputs.file_output_paths(_scene(nodes), notes) == []
    assert len(notes) == 2


def test_sequence_paths_find_every_frame_on_disk(tmp_path) -> None:
    for name in ("plate0001.png", "plate0002.png", "plate10000.png", "plate_ref.png", "other0001.png"):
        (tmp_path / name).write_text("x")
    frames = references.sequence_paths(str(tmp_path / "plate0001.png"))
    assert [Path(p).name for p in frames] == ["plate0001.png", "plate0002.png", "plate10000.png"]
    assert references.sequence_paths(str(tmp_path / "plate_ref.png")) == [
        str(tmp_path / "plate_ref.png")
    ]
    assert references.sequence_paths(str(tmp_path / "gone0001.png")) == [
        str(tmp_path / "gone0001.png")
    ]


def test_sequence_images_volumes_and_caches_expand(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bpy.data, "filepath", str(tmp_path / "shot.blend"))
    for name in ("plate0001.png", "plate0002.png", "smoke_0001.vdb", "smoke_0002.vdb"):
        (tmp_path / name).write_text("x")
    image = SimpleNamespace(filepath="//plate0001.png", packed_file=None, source="SEQUENCE", library=None)
    volume = SimpleNamespace(filepath="//smoke_0001.vdb", packed_file=None, is_sequence=True, library=None)
    single = SimpleNamespace(filepath="//plate0001.png", packed_file=None, source="FILE", library=None)
    assert [os.path.basename(p) for p in references.files_of(image)] == ["plate0001.png", "plate0002.png"]
    assert [os.path.basename(p) for p in references.files_of(volume)] == ["smoke_0001.vdb", "smoke_0002.vdb"]
    assert [os.path.basename(p) for p in references.files_of(single)] == ["plate0001.png"]
