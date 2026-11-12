"""Find the files a scene writes: render frames, compositor File Output images and bakes.

Render frames come from the scene's output path over its frame range.
Compositor File Output nodes, also those inside node groups, give the exact
file of each slot for each frame, named the way Blender 5.2 names them. A
bake owns its folder: every file under it is an output. `frames`,
`files_in`, `inside`, `frame_name` and `file_output_names` are pure Python.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bpy

MAX_FRAMES = 100_000
MAX_FILES = 100_000

# File Output node file_format -> extension Blender writes.
EXTENSIONS = {
    "BMP": ".bmp", "IRIS": ".rgb", "PNG": ".png", "JPEG": ".jpg", "JPEG2000": ".jp2",
    "TARGA": ".tga", "TARGA_RAW": ".tga", "CINEON": ".cin", "DPX": ".dpx",
    "OPEN_EXR": ".exr", "OPEN_EXR_MULTILAYER": ".exr", "HDR": ".hdr", "TIFF": ".tif",
    "WEBP": ".webp",
}  # fmt: skip

_HASHES = re.compile(r"#+")


def frames(start: int, end: int, step: int) -> tuple[list[int], int]:
    """Return the frames a range renders, capped at MAX_FRAMES, and the uncut count."""
    step = max(step, 1)
    total = (end - start) // step + 1 if end >= start else 0
    return list(range(start, start + min(total, MAX_FRAMES) * step, step)), total


def files_in(folder: str, *, recursive: bool) -> list[str]:
    """Return the files in `folder`. A missing folder gives nothing. At most MAX_FILES."""
    if not os.path.isdir(folder):
        return []
    found: list[str] = []
    walker = os.walk(folder) if recursive else [next(os.walk(folder))]
    for current, _dirs, names in walker:
        for name in sorted(names):
            found.append(os.path.join(current, name))
            if len(found) >= MAX_FILES:
                return found
    return found


def inside(values: Iterable[str], root: Path) -> list[Path]:
    """Return the distinct absolute paths among `values` that lie inside `root`."""
    root = Path(root).resolve()
    found: dict[Path, None] = {}
    for value in values:
        if not value.strip():
            continue
        path = Path(os.path.abspath(value)).resolve()
        if path.is_relative_to(root):
            found[path] = None
    return list(found)


def render_paths(scene: Any, notes: list[str] | None = None) -> list[str]:
    """Return the files the scene's render writes over its frame range."""
    render = scene.render
    if not render.filepath.strip():
        return []
    if render.is_movie_format:
        return [render.frame_path(frame=scene.frame_start)]
    numbers, total = frames(scene.frame_start, scene.frame_end, scene.frame_step)
    if total > len(numbers) and notes is not None:
        notes.append(f"Only the first {len(numbers)} of {total} frames were checked.")
    return list(dict.fromkeys(render.frame_path(frame=f) for f in numbers))


def _abs(path: str, library: Any = None) -> str:
    return os.path.normpath(bpy.path.abspath(path, library=library))


@dataclass(frozen=True)
class Slot:
    """One file a File Output node writes per frame.

    Attributes:
        name: The node's file name plus the slot name, which may hold folders.
        extension: The extension with its dot, or "" when the node writes none.
    """

    name: str
    extension: str


def frame_name(name: str, frame: int) -> str:
    """Return `name` with the frame in it the way Blender writes it.

    The last run of `#` becomes the zero-padded frame; without one, a
    four-digit frame is appended.
    """
    runs = list(_HASHES.finditer(name))
    if not runs:
        return f"{name}{frame:04d}"
    last = runs[-1]
    return f"{name[: last.start()]}{frame:0{len(last.group())}d}{name[last.end() :]}"


def file_output_names(directory: str, slots: Iterable[Slot], numbers: Iterable[int]) -> list[str]:
    """Return every file the slots write over `numbers`, inside `directory`."""
    numbers = list(numbers)
    return [
        os.path.normpath(os.path.join(directory, frame_name(slot.name, n) + slot.extension))
        for slot in slots
        for n in numbers
    ]


def _file_output_nodes(tree: Any, seen: set[int]) -> Iterable[Any]:
    if tree is None or id(tree) in seen:
        return
    seen.add(id(tree))
    for node in tree.nodes:
        if node.mute:
            continue
        if node.type == "OUTPUT_FILE":
            yield node
        elif node.type == "GROUP":
            yield from _file_output_nodes(getattr(node, "node_tree", None), seen)


def _slots(node: Any, blend_name: str, notes: list[str] | None) -> list[Slot]:
    base = node.file_name.replace("{blend_name}", blend_name)
    if "{" in base:
        if notes is not None:
            notes.append(f"File Output {node.name}: path variables in {node.file_name!r} are not read.")
        return []
    media = node.format.media_type
    if media == "MULTI_LAYER_IMAGE":
        return [Slot(base, ".exr" if node.use_file_extension else "")]
    if media != "IMAGE":
        if notes is not None:
            notes.append(f"File Output {node.name}: video outputs are not checked.")
        return []
    slots = []
    for item in node.file_output_items:
        fmt = item.format if item.override_node_format else node.format
        extension = EXTENSIONS.get(fmt.file_format, "") if node.use_file_extension else ""
        slots.append(Slot(base + item.name, extension))
    return slots


def file_output_paths(scene: Any, notes: list[str] | None = None) -> list[str]:
    """Return the files the scene's compositor File Output nodes write over its frame range."""
    tree = getattr(scene, "compositing_node_group", None)
    numbers, _total = frames(scene.frame_start, scene.frame_end, scene.frame_step)
    blend_name = Path(bpy.data.filepath).stem if bpy.data.filepath else "Unsaved"
    paths: list[str] = []
    for node in _file_output_nodes(tree, set()):
        if node.directory:
            slots = _slots(node, blend_name, notes)
            paths += file_output_names(_abs(node.directory), slots, numbers)
    return paths


def _point_caches(obj: Any) -> Iterable[Any]:
    for mod in obj.modifiers:
        if mod.type in ("CLOTH", "SOFT_BODY"):
            yield mod.point_cache
        elif mod.type == "PARTICLE_SYSTEM":
            yield mod.particle_system.point_cache
        elif mod.type == "DYNAMIC_PAINT" and mod.canvas_settings is not None:
            for surface in mod.canvas_settings.canvas_surfaces:
                yield surface.point_cache


def bake_folders(scene: Any) -> list[str]:
    """Return the folders the scene's bakes write to: Geometry Nodes, fluid and disk caches."""
    folders: list[str] = []
    caches: list[Any] = []
    for obj in scene.objects:
        for mod in obj.modifiers:
            if mod.type == "NODES":
                if mod.bake_directory:
                    folders.append(_abs(mod.bake_directory, obj.library))
                folders += [
                    _abs(bake.directory, obj.library)
                    for bake in mod.bakes
                    if bake.use_custom_path and bake.directory
                ]
            elif mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
                if mod.domain_settings.cache_directory:
                    folders.append(_abs(mod.domain_settings.cache_directory, obj.library))
        caches += list(_point_caches(obj))
    world = scene.rigidbody_world
    if world is not None and world.point_cache is not None:
        caches.append(world.point_cache)
    for cache in caches:
        if not cache.use_disk_cache:
            continue
        if cache.use_external and cache.filepath:
            folders.append(_abs(cache.filepath))
        elif bpy.data.filepath:
            name = Path(bpy.data.filepath).stem
            folders.append(_abs(f"//blendcache_{name}"))
    return list(dict.fromkeys(folders))


def scene_outputs(scene: Any, root: Path, notes: list[str] | None = None) -> list[Path]:
    """Return every file inside `root` the scene writes or has written.

    Render frames and File Output files are listed whether or not they exist
    yet. Bake folders give the files already in them.
    """
    values = render_paths(scene, notes) + file_output_paths(scene, notes)
    for folder in bake_folders(scene):
        values += files_in(folder, recursive=True)
    return inside(values, root)


def render_outputs(scene: Any, root: Path, notes: list[str] | None = None) -> list[Path]:
    """Return the files inside `root` one render of the scene writes: frames and File Output images."""
    return inside(render_paths(scene, notes) + file_output_paths(scene, notes), root)
