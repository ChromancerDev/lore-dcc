"""Prefs load and save."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from lore_dcc import prefs
from lore_dcc.prefs import Prefs, load_prefs, save_prefs


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load_prefs(tmp_path) == Prefs()
    assert load_prefs(tmp_path / "does" / "not" / "exist") == Prefs()


def test_round_trip_creates_folder_and_writes_version(tmp_path: Path) -> None:
    folder = tmp_path / "nested" / "prefs"
    wanted = Prefs(enabled=False, prompt_lock_before_save=False, keep_files_locked=True)
    save_prefs(folder, wanted)
    assert load_prefs(folder) == wanted
    data = json.loads((folder / prefs.PREFS_FILE).read_text(encoding="utf-8"))
    assert data["version"] == prefs.SCHEMA_VERSION
    assert data["enabled"] is False
    assert not (folder / (prefs.PREFS_FILE + ".tmp")).exists()


def test_save_overwrites_existing(tmp_path: Path) -> None:
    save_prefs(tmp_path, Prefs(push_after_commit=False))
    save_prefs(tmp_path, Prefs(push_after_commit=True))
    assert load_prefs(tmp_path).push_after_commit is True


def test_unknown_keys_ignored_and_missing_keys_default(tmp_path: Path) -> None:
    (tmp_path / prefs.PREFS_FILE).write_text(
        json.dumps({"version": 9, "enabled": False, "future_toggle": True}), encoding="utf-8"
    )
    loaded = load_prefs(tmp_path)
    assert loaded == Prefs(enabled=False)


@pytest.mark.parametrize("text", ["{not json", "", "[1, 2]", "null", "3"])
def test_corrupt_file_falls_back_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, text: str
) -> None:
    (tmp_path / prefs.PREFS_FILE).write_text(text, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert load_prefs(tmp_path) == Prefs()
    assert "Ignoring" in caplog.text


def test_undecodable_file_falls_back(tmp_path: Path) -> None:
    (tmp_path / prefs.PREFS_FILE).write_bytes(b"\xff\xfe\x00bad")
    assert load_prefs(tmp_path) == Prefs()


def test_wrong_typed_value_uses_that_default_only(tmp_path: Path) -> None:
    (tmp_path / prefs.PREFS_FILE).write_text(
        json.dumps({"enabled": "no", "push_after_commit": False, "keep_files_locked": 1}),
        encoding="utf-8",
    )
    assert load_prefs(tmp_path) == Prefs(push_after_commit=False)
