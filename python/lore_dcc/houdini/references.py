"""Find the files a scene reads, and reload them after a sync.

`split_references` is pure Python; the rest reads the scene through `hou`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import hou


@dataclass(frozen=True)
class SceneReferences:
    """The files a scene reads.

    Attributes:
        inside: Absolute paths inside the working copy, in scene order, no
            duplicates. Missing files are kept; folders are not.
        outside: `(where, file)` pairs for existing files outside the
            working copy. `where` is the parm path, or the node type for an
            HDA library.
    """

    inside: tuple[Path, ...]
    outside: tuple[tuple[str, str], ...]


def split_references(pairs: Iterable[tuple[str, str]], root: Path) -> SceneReferences:
    """Sort `(where, expanded value)` pairs into inside and outside the working copy.

    Values that are blank, not absolute, or an existing folder are dropped.
    An outside value is kept only when it is an existing file.
    """
    root = root.resolve()
    inside: dict[Path, None] = {}
    outside: list[tuple[str, str]] = []
    for where, value in pairs:
        if not value.strip() or not os.path.isabs(value) or os.path.isdir(value):
            continue
        resolved = Path(value).resolve()
        if resolved.is_relative_to(root):
            inside[resolved] = None
        elif os.path.isfile(value):
            outside.append((where, value))
    return SceneReferences(tuple(inside), tuple(outside))


def library_file(definition: Any) -> str | None:
    """Return an HDA definition's library file, or None when it lives inside the hip."""
    if definition is None:
        return None
    file = definition.libraryFilePath()
    return file if file and file != "Embedded" and os.path.isabs(file) else None


def scan(root: Path) -> SceneReferences:
    """Return every file the open scene reads, from `hou.fileReferences()` plus
    the library files of the HDA node types the scene uses.

    Each parm is evaluated at the current frame only. HDA libraries count
    only inside the working copy, so Houdini's own never show as outside.
    """
    pairs: list[tuple[str, str]] = []
    for parm, _raw in hou.fileReferences():
        if parm is None:
            continue
        try:
            # ponytail: current frame only; evaluate the frame range if sequences need links
            pairs.append((parm.path(), parm.evalAsString()))
        except hou.Error:
            continue
    scene = split_references(pairs, root)
    seen = {_key(p) for p in scene.inside}
    libraries: list[Path] = []
    for node in hou.node("/").allSubChildren():
        file = library_file(node.type().definition())
        if file is None or _key(file) in seen:
            continue
        seen.add(_key(file))
        if Path(file).resolve().is_relative_to(root.resolve()):
            libraries.append(Path(file).resolve())
    return SceneReferences((*scene.inside, *libraries), scene.outside)


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.realpath(path))


def reload(paths: Sequence[Path]) -> None:
    """Make Houdini re-read files a sync just updated, without reloading the hip.

    Runs `opupdate`, which re-cooks nodes whose files changed, and
    `hou.hda.reloadFile` for each path that is a loaded HDA library.
    """
    hou.hscript("opupdate")
    loaded = {_key(file) for file in hou.hda.loadedFiles()}
    for path in paths:
        if _key(path) in loaded:
            hou.hda.reloadFile(str(path))
