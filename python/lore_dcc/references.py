"""Sort the files a scene reads into inside and outside the working copy. Stdlib only."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SceneReferences:
    """The files a scene reads.

    Attributes:
        inside: Absolute paths inside the working copy, in scene order, no
            duplicates. Missing files are kept; folders are not.
        outside: `(where, file)` pairs for existing files outside the
            working copy. `where` names what in the scene reads the file.
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
