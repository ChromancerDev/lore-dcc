"""Per-user preferences, stored as JSON in a folder the host DCC chooses."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

logger = logging.getLogger(__name__)

PREFS_FILE = "lore_dcc_prefs.json"
SCHEMA_VERSION = 1


@dataclass
class Prefs:
    """Per-user toggles. Every field has a default, so an older file still loads.

    Attributes:
        enabled: Master switch. When False, hooks do nothing and menus say so.
        prompt_lock_before_save: Ask "Lock and save?" before saving an unlocked tracked file.
        prompt_stage_after_save: Ask "Stage it?" after saving a new file.
        push_after_commit: Default for the Commit dialog's checkbox.
        keep_files_locked: Default for the Commit dialog's checkbox.
    """

    enabled: bool = True
    prompt_lock_before_save: bool = True
    prompt_stage_after_save: bool = True
    push_after_commit: bool = True
    keep_files_locked: bool = False


def load_prefs(folder: Path) -> Prefs:
    """Read prefs from `folder / PREFS_FILE`, or return defaults.

    Unknown keys are ignored and a corrupt file falls back to defaults with a
    logged warning, so a bad prefs file never stops the plugin. A value of the
    wrong type falls back to that field's default.
    """
    path = Path(folder) / PREFS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Prefs()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring unreadable prefs file %s: %s", path, exc)
        return Prefs()
    if not isinstance(data, dict):
        logger.warning("Ignoring prefs file %s: top level is not an object", path)
        return Prefs()
    values = {}
    for item in fields(Prefs):
        if item.name not in data:
            continue
        if isinstance(data[item.name], bool):
            values[item.name] = data[item.name]
        else:
            logger.warning("Ignoring prefs key %s in %s: not a boolean", item.name, path)
    return Prefs(**values)


def save_prefs(folder: Path, prefs: Prefs) -> None:
    """Write prefs to `folder / PREFS_FILE` with the schema version.

    Creates the folder if needed. The write goes through a temporary file, so a
    crash never leaves a half-written prefs file.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PREFS_FILE
    temp = path.with_name(path.name + ".tmp")
    payload = {"version": SCHEMA_VERSION, **asdict(prefs)}
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temp, path)
