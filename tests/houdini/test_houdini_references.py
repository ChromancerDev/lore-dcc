"""Reference splitting, scanning and reloading with a stand-in `hou`."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from lore_dcc.houdini import references


def test_split_keeps_inside_files_in_order_without_duplicates_or_folders(tmp_path: Path) -> None:
    root = tmp_path / "work"
    (root / "geo").mkdir(parents=True)
    existing = root / "geo" / "a.bgeo"
    existing.write_text("x")
    missing = root / "geo" / "gone.bgeo"
    scene = references.split_references(
        [
            ("/obj/a:file", str(missing)),
            ("/obj/b:file", str(existing)),
            ("/obj/c:file", str(missing)),
            ("/obj/d:basedir", str(root / "geo")),
        ],
        root,
    )
    assert scene.inside == (missing.resolve(), existing.resolve())
    assert scene.outside == ()


def test_split_drops_blank_and_relative_values(tmp_path: Path) -> None:
    scene = references.split_references(
        [("a", ""), ("b", "   "), ("c", "geo/a.bgeo"), ("d", "$HIP/a.bgeo")], tmp_path
    )
    assert scene == references.SceneReferences((), ())


def test_split_outside_keeps_only_existing_files(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real = elsewhere / "tex.exr"
    real.write_text("x")
    scene = references.split_references(
        [
            ("/mat/a:file", str(real)),
            ("/mat/b:file", str(elsewhere / "gone.exr")),
            ("/mat/c:dir", str(elsewhere)),
        ],
        root,
    )
    assert scene.inside == ()
    assert scene.outside == (("/mat/a:file", str(real)),)


def test_split_does_not_treat_a_sibling_prefix_as_inside(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    sibling = tmp_path / "work2"
    sibling.mkdir()
    (sibling / "a.bgeo").write_text("x")
    scene = references.split_references([("p", str(sibling / "a.bgeo"))], root)
    assert scene.inside == ()
    assert len(scene.outside) == 1


class FakeParm:
    def __init__(self, path: str, value: str | Exception) -> None:
        self._path = path
        self._value = value

    def path(self) -> str:
        return self._path

    def evalAsString(self) -> str:
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


def fake_node(type_name: str, library: str | None) -> SimpleNamespace:
    definition = SimpleNamespace(libraryFilePath=lambda: library) if library is not None else None
    node_type = SimpleNamespace(definition=lambda: definition, nameWithCategory=lambda: type_name)
    return SimpleNamespace(type=lambda: node_type)


def test_scan_adds_inside_hda_libraries_once_and_ignores_outside_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "ext.hda"
    outside.write_text("x")
    inside = root / "tool.hda"
    inside.write_text("x")
    geo = root / "a.bgeo"
    nodes = [
        fake_node("Sop/tool::1.0", str(inside)),
        fake_node("Sop/tool::1.0", str(inside)),
        fake_node("Sop/ext", str(outside)),
        fake_node("Sop/embedded", "Embedded"),
        fake_node("Sop/box", None),
    ]
    parms = [
        (FakeParm("/obj/geo1/file1/file", str(geo)), "$HIP/a.bgeo"),
        (None, "ignored"),
        (FakeParm("/obj/geo1/bad/file", RuntimeError("boom")), "x"),
    ]
    monkeypatch.setattr(references.hou, "fileReferences", lambda: parms, raising=False)
    monkeypatch.setattr(
        references.hou,
        "node",
        lambda path: SimpleNamespace(allSubChildren=lambda: nodes),
        raising=False,
    )
    scene = references.scan(root)
    assert scene.inside == (geo.resolve(), inside.resolve())
    assert scene.outside == ()


def test_reload_runs_opupdate_then_reloads_only_loaded_libraries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, str]] = []
    loaded = tmp_path / "tool.hda"
    other = tmp_path / "a.bgeo"
    monkeypatch.setattr(
        references.hou, "hscript", lambda cmd: calls.append(("hscript", cmd)), raising=False
    )
    monkeypatch.setattr(
        references.hou,
        "hda",
        SimpleNamespace(
            loadedFiles=lambda: [os.path.normcase(str(loaded))],
            reloadFile=lambda path: calls.append(("reload", path)),
        ),
        raising=False,
    )
    references.reload([other, loaded])
    assert calls == [("hscript", "opupdate"), ("reload", str(loaded))]


def test_library_file_ignores_embedded_and_missing_definitions() -> None:
    assert references.library_file(None) is None
    assert references.library_file(SimpleNamespace(libraryFilePath=lambda: "Embedded")) is None
    assert references.library_file(SimpleNamespace(libraryFilePath=lambda: "")) is None
    absolute = os.path.abspath("lib.hda")
    assert references.library_file(SimpleNamespace(libraryFilePath=lambda: absolute)) == absolute
