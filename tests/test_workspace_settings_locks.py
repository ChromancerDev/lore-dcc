"""Tests for working copy discovery, per-repo settings and lock classification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lore_dcc import locks, settings, workspace
from lore_dcc.errors import NotAWorkingCopy, SettingsError
from lore_dcc.locks import Identity, LockRecord, LockState


def make_repo(root: Path, config: str = "") -> Path:
    (root / ".lore").mkdir(parents=True)
    (root / ".lore" / "config.toml").write_text(config, encoding="utf-8")
    return root


def test_find_workspace_walks_up(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "proj", 'remote_url = "lore://example:41337"\nidentity = "me"\n')
    found = workspace.find_workspace(root / "scenes" / "not_saved_yet.hip")
    assert found.root == root.resolve()
    assert (found.remote_url, found.identity) == ("lore://example:41337", "me")
    assert found.relative(root / "scenes" / "shot.hip") == "scenes/shot.hip"


def test_find_workspace_without_config(tmp_path: Path) -> None:
    (tmp_path / ".lore").mkdir()
    found = workspace.find_workspace(tmp_path)
    assert (found.remote_url, found.identity) == (None, None)


def test_not_a_working_copy(tmp_path: Path) -> None:
    with pytest.raises(NotAWorkingCopy):
        workspace.find_workspace(tmp_path / "a.hip")


def test_relative_outside(tmp_path: Path) -> None:
    found = workspace.find_workspace(make_repo(tmp_path / "proj"))
    with pytest.raises(NotAWorkingCopy):
        found.relative(tmp_path / "elsewhere.hip")


def test_default_settings(tmp_path: Path) -> None:
    current = settings.load_settings(tmp_path)
    assert current.is_lockable("caches/sim.0001.bgeo.sc")
    assert current.is_lockable("SHOT.HIP")
    assert not current.is_lockable("notes.txt")


def test_settings_override(tmp_path: Path) -> None:
    data = {"version": 1, "lockable": {"add": [".SIM"], "remove": [".obj"]}}
    (tmp_path / settings.SETTINGS_FILE).write_text(json.dumps(data), encoding="utf-8")
    current = settings.load_settings(tmp_path)
    assert current.is_lockable("a.sim")
    assert not current.is_lockable("a.obj")


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"version": 2},
        {"version": 1, "lockabel": {}},
        {"version": 1, "lockable": {"add": ["sim"]}},
        {"version": 1, "lockable": {"extra": []}},
    ],
)
def test_settings_rejects(data: object) -> None:
    with pytest.raises(SettingsError):
        settings.parse_settings(data)


def test_settings_bad_json(tmp_path: Path) -> None:
    (tmp_path / settings.SETTINGS_FILE).write_text("{", encoding="utf-8")
    with pytest.raises(SettingsError):
        settings.load_settings(tmp_path)


ME = Identity.of("me", "user:123", None, " ")
LOCKS = (
    LockRecord("mine.hip", "user:123", "main"),
    LockRecord("theirs.hip", "user:999", "main"),
    LockRecord("blank.hip", "", "main"),
)


@pytest.mark.parametrize(
    ("path", "found", "expected"),
    [
        ("mine.hip", LOCKS, LockState.LOCKED_BY_ME),
        ("theirs.hip", LOCKS, LockState.LOCKED_BY_OTHER),
        ("blank.hip", LOCKS, LockState.UNKNOWN),
        ("free.hip", LOCKS, LockState.NOT_LOCKED),
        ("mine.hip", None, LockState.UNKNOWN),
    ],
)
def test_classify(path: str, found: tuple[LockRecord, ...] | None, expected: LockState) -> None:
    assert locks.classify(path, found, ME) is expected


def test_houdini_block_is_stored_raw(tmp_path: Path) -> None:
    block = {"outputs": [{"node": "filecache", "parm": "file"}], "anything": [1, {"a": None}]}
    (tmp_path / "lore-dcc.json").write_text(
        json.dumps({"version": 1, "houdini": block}), encoding="utf-8"
    )
    loaded = settings.load_settings(tmp_path)
    assert loaded.houdini == block
    assert loaded.is_lockable("a.hip")


def test_houdini_defaults_to_empty_dict(tmp_path: Path) -> None:
    assert settings.load_settings(tmp_path).houdini == {}
    assert settings.parse_settings({"version": 1}).houdini == {}


@pytest.mark.parametrize("value", [[], "x", 3, None])
def test_houdini_must_be_an_object(value: object) -> None:
    with pytest.raises(SettingsError, match="houdini must be an object"):
        settings.parse_settings({"version": 1, "houdini": value})


def test_unknown_keys_still_rejected_next_to_houdini() -> None:
    with pytest.raises(SettingsError, match="unknown keys"):
        settings.parse_settings({"version": 1, "houdini": {}, "blender": {}})


def test_classify_ignores_locks_on_other_branches() -> None:
    records = (
        LockRecord("a.hip", "user:999", "other"),
        LockRecord("b.hip", "user:999", "other"),
        LockRecord("b.hip", "user:123", "main"),
        LockRecord("c.hip", "user:999", None),
    )
    assert locks.classify("a.hip", records, ME, "main") is LockState.NOT_LOCKED
    assert locks.classify("b.hip", records, ME, "main") is LockState.LOCKED_BY_ME
    assert locks.classify("c.hip", records, ME, "main") is LockState.LOCKED_BY_OTHER
    assert locks.classify("a.hip", records, ME) is LockState.LOCKED_BY_OTHER


def test_classify_compares_paths_case_insensitively_only_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = (LockRecord("Scenes/Shot.hip", "user:999", "main"),)
    monkeypatch.setattr(locks, "CASE_INSENSITIVE", True)
    assert locks.classify("scenes/shot.HIP", records, ME) is LockState.LOCKED_BY_OTHER
    assert locks.path_key("A/B.hip") == "a/b.hip"
    monkeypatch.setattr(locks, "CASE_INSENSITIVE", False)
    assert locks.classify("scenes/shot.HIP", records, ME) is LockState.NOT_LOCKED
    assert locks.path_key("A/B.hip") == "A/B.hip"
