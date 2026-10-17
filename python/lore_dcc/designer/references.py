"""Find the files a package reads, and reload them after a sync.

Reads the package through `sd`; the sorting is `lore_dcc.references`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lore_dcc.designer import packages
from lore_dcc.references import SceneReferences, split_references

__all__ = ["SceneReferences", "linked_file", "reload", "scan"]

LINKED = ("Linked", "CopiedAndLinked")
PACKAGE_SUFFIXES = (".sbs", ".sbsar")
IMAGE_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".exr", ".hdr", ".psd", ".bmp", ".dds",
    ".webp", ".gif",
)  # fmt: skip


def linked_file(resource: Any) -> str | None:
    """Return the file a linked resource reads, or None for an embedded one."""
    method = resource.getEmbedMethod()
    if getattr(method, "name", str(method)) not in LINKED:
        return None
    return resource.getFilePath() or None


def scan(package: Any, root: Path) -> SceneReferences:
    """Return every file `package` reads: the packages it depends on and its linked resources.

    Files inside Designer's install folder, such as its own library, are dropped.
    """
    own = packages.key(packages.file_of(package) or "")
    pairs: list[tuple[str, str]] = []
    for dependency in package.getDependencies():
        file = dependency.getFilePath()
        if file:
            pairs.append((f"Dependency {Path(file).name}", file))
    for resource in package.getChildrenResources(True):
        file = linked_file(resource)
        if file:
            pairs.append((f"{type(resource).__name__} {resource.getIdentifier()}", file))
    library = packages.install_dir()
    kept = [
        (where, file)
        for where, file in pairs
        if packages.key(file) != own and not packages.is_under(file, library)
    ]
    return split_references(kept, root)


def reload(paths: Sequence[Path]) -> tuple[list[Path], list[Path]]:
    """Make Designer re-read files a sync just updated.

    Designer re-reads a changed linked bitmap on its own. A changed package
    reloads by unloading and loading the open package that is it or depends
    on it, which also closes that package's graph views. A package with
    unsaved changes is kept as it is. Fonts, SVGs and meshes have no reload.

    Returns:
        The packages that were reloaded, and the synced paths that need the
        user to reopen a package.
    """
    reloaded: list[Path] = []
    pending: list[Path] = []
    done: set[str] = set()
    for path in paths:
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            continue
        if suffix not in PACKAGE_SUFFIXES:
            pending.append(path)
            continue
        owners = [path] if packages.find(path) is not None else packages.dependents(path)
        for owner in owners:
            if packages.key(owner) in done:
                continue
            done.add(packages.key(owner))
            if packages.reload(owner):
                reloaded.append(Path(owner))
            elif path not in pending:
                pending.append(path)
    return reloaded, pending
