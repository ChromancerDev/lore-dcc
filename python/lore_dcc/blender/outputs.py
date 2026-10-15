"""Find the files a scene writes: render frames, compositor File Output images and bakes.

Render frames come from the scene's output path over its frame range. A
compositor File Output node and a bake each own a folder: the images in a
File Output folder, and every file under a bake folder, are its outputs.
`frames`, `files_in` and `inside` are pure Python.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import bpy

MAX_FRAMES = 100_000
MAX_FILES = 100_000

IMAGE_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".tga", ".bmp", ".hdr", ".webp",
        ".dpx", ".cin", ".jp2", ".j2c", ".rgb", ".sgi",
        ".mp4", ".mkv", ".mov", ".avi", ".ogv", ".webm", ".mpg", ".flv",
    }
)  # fmt: skip


def frames(start: int, end: int, step: int) -> tuple[list[int], int]:
    """Return the frames a range renders, capped at MAX_FRAMES, and the uncut count."""
    step = max(step, 1)
    total = (end - start) // step + 1 if end >= start else 0
    return list(range(start, start + min(total, MAX_FRAMES) * step, step)), total


def files_in(folder: str, *, recursive: bool, suffixes: frozenset[str] | None = None) -> list[str]:
    """Return the files in `folder`, optionally only those with one of `suffixes`.

    A missing folder gives nothing. At most MAX_FILES files are returned.
    """
    if not os.path.isdir(folder):
        return []
    found: list[str] = []
    walker = os.walk(folder) if recursive else [next(os.walk(folder))]
    for current, _dirs, names in walker:
        for name in sorted(names):
            if suffixes is None or os.path.splitext(name)[1].lower() in suffixes:
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


def file_output_folders(scene: Any) -> list[str]:
    """Return the folders the scene's compositor File Output nodes write to."""
    tree = getattr(scene, "compositing_node_group", None)
    if tree is None:
        return []
    return [
        _abs(node.directory)
        for node in tree.nodes
        if node.bl_idname == "CompositorNodeOutputFile" and not node.mute and node.directory
    ]


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

    Render frames are listed whether or not they exist yet. File Output and
    bake folders give the files already in them.
    """
    values = render_paths(scene, notes)
    for folder in file_output_folders(scene):
        values += files_in(folder, recursive=False, suffixes=IMAGE_SUFFIXES)
    for folder in bake_folders(scene):
        values += files_in(folder, recursive=True)
    return inside(values, root)


def render_outputs(scene: Any, root: Path, notes: list[str] | None = None) -> list[Path]:
    """Return the files inside `root` one render of the scene writes: frames and File Output images."""
    values = render_paths(scene, notes)
    for folder in file_output_folders(scene):
        values += files_in(folder, recursive=False, suffixes=IMAGE_SUFFIXES)
    return inside(values, root)
