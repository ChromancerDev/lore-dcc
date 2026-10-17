"""Find, inspect and reload Designer packages through `sd`. Main thread only."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import sd

from lore_dcc.designer import host


def install_dir() -> Path:
    """Return Designer's install folder, which holds its own library packages."""
    return Path(sd.__file__).resolve().parents[3]


def key(path: Path | str) -> str:
    """Return a comparison key for a file path."""
    return os.path.normcase(os.path.realpath(path))


def is_under(path: Path | str, folder: Path | str) -> bool:
    """Return True when `path` is `folder` or inside it."""
    try:
        return os.path.commonpath([key(path), key(folder)]) == key(folder)
    except ValueError:
        return False


def file_of(package: Any) -> str | None:
    """Return the package's file, or None while it was never saved."""
    path = package.getFilePath() if package is not None else ""
    return path or None


def current_graph() -> Any:
    """Return the graph that last had focus in a graph view, or None."""
    ui = host.app().getUIMgr()
    return ui.getCurrentGraph() if ui is not None else None


def current() -> Any:
    """Return the package of the graph in focus, or None."""
    graph = current_graph()
    return graph.getPackage() if graph is not None else None


def loaded() -> list[Any]:
    """Return the user packages open in Designer."""
    return list(host.app().getPackageMgr().getUserPackages())


def find(path: Path | str) -> Any:
    """Return the open user package saved at `path`, or None."""
    wanted = key(path)
    for package in loaded():
        file = file_of(package)
        if file and key(file) == wanted:
            return package
    return None


def dependents(path: Path | str) -> list[str]:
    """Return the files of the open user packages that depend on the package at `path`."""
    wanted = key(path)
    found: list[str] = []
    for package in loaded():
        file = file_of(package)
        if file and any(
            dep.getFilePath() and key(dep.getFilePath()) == wanted
            for dep in package.getDependencies()
        ):
            found.append(file)
    return found


def is_modified(path: Path | str) -> bool:
    """Return True when the open package at `path` has unsaved changes."""
    package = find(path)
    return bool(package is not None and package.isModified())


def reload(path: Path | str, *, discard_edits: bool = False) -> bool:
    """Unload and load the open package at `path` so Designer reads it from disk.

    A package with unsaved changes is left alone unless `discard_edits`.
    Returns True when the package was reloaded.
    """
    package = find(path)
    if package is None or (package.isModified() and not discard_edits):
        return False
    manager = host.app().getPackageMgr()
    manager.unloadUserPackage(package)
    manager.loadUserPackage(str(path))
    return True
