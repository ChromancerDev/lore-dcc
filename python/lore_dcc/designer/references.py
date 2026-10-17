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

    Open packages without unsaved changes are unloaded and loaded. A package
    file that is not open needs nothing. Designer has no reload for bitmaps,
    fonts, SVGs or meshes, and a package with unsaved changes is kept as it is.

    Returns:
        The reloaded paths, and the paths that need the user to reopen a package.
    """
    reloaded: list[Path] = []
    pending: list[Path] = []
    for path in paths:
        if packages.reload(path):
            reloaded.append(path)
        elif packages.find(path) is not None or path.suffix.lower() not in PACKAGE_SUFFIXES:
            pending.append(path)
    return reloaded, pending
