"""Find the Lore working copy that holds a file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from lore_dcc.errors import NotAWorkingCopy

LORE_DIR = ".lore"
CONFIG_FILE = "config.toml"


@dataclass(frozen=True)
class Workspace:
    """A Lore working copy.

    Attributes:
        root: The folder that holds `.lore/`.
        remote_url: `remote_url` from `.lore/config.toml`, if set.
        identity: `identity` from `.lore/config.toml`, if set.
    """

    root: Path
    remote_url: str | None
    identity: str | None

    def relative(self, path: Path) -> str:
        """Return `path` relative to the root, with forward slashes.

        Raises:
            NotAWorkingCopy: `path` is outside this working copy.
        """
        try:
            return Path(path).resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise NotAWorkingCopy(f"{path} is outside {self.root}") from exc


def find_workspace(start: Path) -> Workspace:
    """Walk up from a file or folder to the nearest Lore working copy.

    Args:
        start: A file or folder, which need not exist yet.

    Returns:
        The working copy that holds `start`.

    Raises:
        NotAWorkingCopy: No `.lore/` folder above `start`.
    """
    start = Path(start).resolve()
    for folder in (start, *start.parents):
        if (folder / LORE_DIR).is_dir():
            return _read_workspace(folder)
    raise NotAWorkingCopy(f"Not a Lore working copy: {start}")


def _read_workspace(root: Path) -> Workspace:
    config_path = root / LORE_DIR / CONFIG_FILE
    config: dict = {}
    if config_path.is_file():
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    remote_url = config.get("remote_url")
    identity = config.get("identity")
    return Workspace(
        root=root,
        remote_url=remote_url if isinstance(remote_url, str) else None,
        identity=identity if isinstance(identity, str) else None,
    )
