"""Find the files the open .blend reads, and reload them after a sync.

References are the external files of linked libraries, images (every UDIM
tile and every frame of a sequence on disk), sounds, movie clips, Alembic and USD cache files, fonts, volumes and
external text files. Packed and generated data, and Blender's own bundled
files, are not references. `udim_paths`, `sequence_paths` and `own_file` are
pure Python.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bpy

from lore_dcc.references import SceneReferences, split_references

# bpy.data collection, label for messages
KINDS = (
    ("libraries", "Library"),
    ("images", "Image"),
    ("sounds", "Sound"),
    ("movieclips", "Movie clip"),
    ("cache_files", "Cache file"),
    ("fonts", "Font"),
    ("volumes", "Volume"),
    ("texts", "Text"),
)

_TRAILING_DIGITS = re.compile(r"^(.*?)(\d+)$")
_UDIM = "<UDIM>"
_UVTILE = "<UVTILE>"


def udim_paths(path: str, tiles: Iterable[int]) -> list[str]:
    """Return one path per tile for a `<UDIM>` or `<UVTILE>` path, else `[path]`."""
    if _UDIM not in path and _UVTILE not in path:
        return [path]
    found = []
    for tile in tiles:
        u, v = (tile - 1001) % 10 + 1, (tile - 1001) // 10 + 1
        found.append(path.replace(_UDIM, str(tile)).replace(_UVTILE, f"u{u}_v{v}"))
    return found


def sequence_paths(path: str) -> list[str]:
    """Return every frame on disk of the sequence whose frame `path` names, sorted.

    The frame number is the last run of digits before the extension; any
    file in the same folder with the same text around a run of at least as
    many digits is a frame. A name without digits, or a sequence with no frame
    on disk, gives `[path]`.
    """
    folder, name = os.path.split(path)
    stem, extension = os.path.splitext(name)
    match = _TRAILING_DIGITS.match(stem)
    if match is None:
        return [path]
    head, digits = match.groups()
    pattern = re.compile(
        f"{re.escape(head)}\\d{{{len(digits)},}}{re.escape(extension)}", re.IGNORECASE
    )
    try:
        names = os.listdir(folder)
    except OSError:
        return [path]
    found = sorted(os.path.join(folder, n) for n in names if pattern.fullmatch(n))
    return found or [path]


def own_file(path: str, own_folders: Sequence[str]) -> bool:
    """Return True when `path` lies in one of Blender's own install folders."""
    key = os.path.normcase(os.path.abspath(path))
    return any(
        key == folder or key.startswith(folder.rstrip("\\/") + os.sep) for folder in own_folders
    )


def _own_folders() -> list[str]:
    folders = []
    for kind in ("LOCAL", "SYSTEM"):
        folder = bpy.utils.resource_path(kind)
        if folder and os.path.isdir(folder):
            folders.append(os.path.normcase(os.path.abspath(folder)))
    return folders


def files_of(block: Any) -> list[str]:
    """Return the absolute files a data-block reads from disk; none when packed or generated."""
    filepath = getattr(block, "filepath", "")
    if not filepath or getattr(block, "packed_file", None) is not None:
        return []
    if getattr(block, "source", "FILE") in ("GENERATED", "VIEWER"):
        return []
    if getattr(block, "is_in_memory", False) or filepath == "<builtin>":
        return []
    path = os.path.normpath(bpy.path.abspath(filepath, library=block.library))
    if getattr(block, "source", "") == "TILED":
        return udim_paths(path, [tile.number for tile in block.tiles])
    if getattr(block, "source", "") == "SEQUENCE" or getattr(block, "is_sequence", False):
        return sequence_paths(path)
    return [path]


def blocks() -> Iterator[tuple[str, Any, list[str]]]:
    """Yield `(label, data-block, files)` for every data-block that reads files."""
    own = _own_folders()
    for attr, kind in KINDS:
        for block in getattr(bpy.data, attr, ()):
            files = [f for f in files_of(block) if not own_file(f, own)]
            if files:
                yield f"{kind} {block.name}", block, files


def scan(root: Path) -> SceneReferences:
    """Return every file the open .blend reads, sorted into inside and outside `root`."""
    pairs = [(label, file) for label, _block, files in blocks() for file in files]
    return split_references(pairs, root)


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.realpath(path))


@dataclass(frozen=True)
class Reloaded:
    """What `reload` could not do.

    Attributes:
        stale: Labels of data-blocks Blender cannot reload while the file is
            open, such as sounds, movie clips and fonts.
        unsaved: Labels of images with unsaved paint, left as they are so the
            painted pixels survive.
    """

    stale: tuple[str, ...] = ()
    unsaved: tuple[str, ...] = ()


def reload(paths: Sequence[Path | str]) -> Reloaded:
    """Make Blender re-read files a sync just updated, without reloading the .blend.

    Libraries and images reload; cache files and volumes re-read their path.
    An image with unsaved paint is never reloaded.
    """
    wanted = {_key(p) for p in paths}
    stale: list[str] = []
    unsaved: list[str] = []
    for label, block, files in list(blocks()):
        if not any(_key(f) in wanted for f in files):
            continue
        if getattr(block, "is_dirty", False):
            unsaved.append(label)
        elif hasattr(block, "reload"):
            block.reload()
        elif block.bl_rna.identifier in ("CacheFile", "Volume"):
            block.filepath = block.filepath
        else:
            stale.append(label)
    return Reloaded(tuple(stale), tuple(unsaved))
