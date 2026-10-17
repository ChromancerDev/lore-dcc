"""References of a package, and reloading after a sync, against the fake `sd`."""

from __future__ import annotations

from pathlib import Path

from fake_sd import (
    INSTALL,
    EmbedMethod,
    FakeDependency,
    FakePackage,
    FakeResource,
    SDResourceBitmap,
)
from lore_dcc.designer import packages, references


def _touch(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return str(path)


def test_scan_keeps_dependencies_and_linked_resources_in_the_working_copy(app, tmp_path: Path) -> None:
    root = tmp_path / "work"
    own = _touch(root / "mat" / "rock.sbs")
    outside = _touch(tmp_path / "elsewhere" / "noise.png")
    package = FakePackage(
        own,
        dependencies=[
            FakeDependency(_touch(root / "lib" / "shared.sbs")),
            FakeDependency(own),
            FakeDependency("sbs://pattern_perlin_noise.sbs"),
            FakeDependency(str(INSTALL / "resources" / "packages" / "blend.sbs")),
        ],
        resources=[
            SDResourceBitmap("albedo", _touch(root / "tex" / "albedo.png")),
            FakeResource("font", _touch(root / "fonts" / "a.ttf"), EmbedMethod.CopiedAndLinked),
            SDResourceBitmap("baked", str(root / "tex" / "baked.png"), EmbedMethod.BinaryEmbedded),
            SDResourceBitmap("ext", outside),
        ],
    )
    scene = references.scan(package, root)
    assert scene.inside == (
        (root / "lib" / "shared.sbs").resolve(),
        (root / "tex" / "albedo.png").resolve(),
        (root / "fonts" / "a.ttf").resolve(),
    )
    assert scene.outside == (("SDResourceBitmap ext", outside),)


def test_reload_reloads_open_packages_and_the_packages_that_use_a_synced_one(
    app, tmp_path: Path
) -> None:
    clean = FakePackage(str(tmp_path / "clean.sbs"))
    dirty = FakePackage(str(tmp_path / "dirty.sbs"), modified=True)
    shared = tmp_path / "shared.sbs"
    user = FakePackage(str(tmp_path / "user.sbs"), dependencies=[FakeDependency(str(shared))])
    app.packages = [clean, dirty, user]
    closed = tmp_path / "closed.sbs"
    bitmap = tmp_path / "albedo.png"
    font = tmp_path / "title.ttf"
    reloaded, pending = references.reload(
        [Path(clean.file), Path(dirty.file), shared, closed, bitmap, font]
    )
    assert reloaded == [Path(clean.file), Path(user.file)]
    assert pending == [Path(dirty.file), font]
    assert app.events == [
        ("unload", clean.file), ("load", clean.file), ("unload", user.file), ("load", user.file),
    ]  # fmt: skip


def test_reload_with_discard_edits_reloads_a_modified_package(app, tmp_path: Path) -> None:
    dirty = FakePackage(str(tmp_path / "dirty.sbs"), modified=True)
    app.packages = [dirty]
    assert packages.reload(dirty.file, discard_edits=True)
    assert app.events == [("unload", dirty.file), ("load", dirty.file)]


def test_current_package_comes_from_the_graph_in_focus(app, tmp_path: Path) -> None:
    assert packages.current() is None
    app.current = FakePackage(str(tmp_path / "a.sbs"))
    assert packages.current() is app.current
    assert packages.file_of(FakePackage("")) is None
