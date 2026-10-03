"""Per-repo settings from an optional `lore-dcc.json` at the working copy root."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lore_dcc.errors import SettingsError

SETTINGS_FILE = "lore-dcc.json"
SCHEMA_VERSION = 1

DEFAULT_LOCKABLE: tuple[str, ...] = (
    ".hip", ".hiplc", ".hipnc",
    ".hda", ".hdalc", ".hdanc", ".otl", ".otllc", ".otlnc",
    ".bgeo", ".bgeo.sc", ".bgeo.gz", ".geo",
    ".abc",
    ".usd", ".usda", ".usdc", ".usdz",
    ".vdb",
    ".fbx", ".obj", ".gltf", ".glb",
    ".exr",
)  # fmt: skip


@dataclass(frozen=True)
class Settings:
    """Settings for one working copy.

    Attributes:
        lockable: Lower-case suffixes of files the plugin locks, longest first.
    """

    lockable: tuple[str, ...]

    def is_lockable(self, path: str | Path) -> bool:
        """Return True when the file name ends in a lockable suffix."""
        name = Path(path).name.lower()
        return any(name.endswith(suffix) for suffix in self.lockable)


def load_settings(root: Path) -> Settings:
    """Read `lore-dcc.json` from a working copy root, or return the defaults.

    Raises:
        SettingsError: The file exists but is not valid.
    """
    path = Path(root) / SETTINGS_FILE
    if not path.is_file():
        return _build(set(), set())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettingsError(f"{path}: {exc}") from exc
    return parse_settings(data, source=str(path))


def parse_settings(data: object, source: str = SETTINGS_FILE) -> Settings:
    """Validate the parsed JSON of a settings file.

    Raises:
        SettingsError: Unknown keys, a wrong version, or wrong value types.
    """
    if not isinstance(data, dict):
        raise SettingsError(f"{source}: top level must be an object")
    unknown = set(data) - {"version", "lockable"}
    if unknown:
        raise SettingsError(f"{source}: unknown keys {sorted(unknown)}")
    if data.get("version") != SCHEMA_VERSION:
        raise SettingsError(f"{source}: version must be {SCHEMA_VERSION}")
    lockable = data.get("lockable", {})
    if not isinstance(lockable, dict) or set(lockable) - {"add", "remove"}:
        raise SettingsError(f"{source}: lockable must be an object with add and remove")
    add = _suffixes(lockable.get("add", []), f"{source}: lockable.add")
    remove = _suffixes(lockable.get("remove", []), f"{source}: lockable.remove")
    return _build(add, remove)


def _suffixes(value: object, where: str) -> set[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.startswith(".") and len(item) > 1 for item in value
    ):
        raise SettingsError(f"{where} must be a list of suffixes like \".bgeo.sc\"")
    return {item.lower() for item in value}


def _build(add: set[str], remove: set[str]) -> Settings:
    suffixes = (set(DEFAULT_LOCKABLE) | add) - remove
    return Settings(lockable=tuple(sorted(suffixes, key=lambda s: (-len(s), s))))
