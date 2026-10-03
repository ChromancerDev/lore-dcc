"""Tests for working copy discovery, per-repo settings and the read-only rule."""

from __future__ import annotations

import json
import os
import stat
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


@pytest.mark.parametrize(
    ("state", "tracked", "lockable", "expected"),
    [
        (LockState.NOT_LOCKED, True, True, True),
        (LockState.LOCKED_BY_ME, True, True, False),
        (LockState.LOCKED_BY_OTHER, True, True, True),
        (LockState.LOCKED_BY_OTHER, False, True, True),
        (LockState.NOT_LOCKED, False, True, False),
        (LockState.UNKNOWN, True, True, None),
        (LockState.NOT_LOCKED, True, False, None),
    ],
)
def test_wanted_read_only(state: LockState, tracked: bool, lockable: bool, expected: bool | None) -> None:
    assert locks.wanted_read_only(state, tracked=tracked, lockable=lockable) is expected


def test_set_read_only_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "a.hip"
    path.write_bytes(b"x")
    assert locks.set_read_only(path, True)
    assert not os.stat(path).st_mode & stat.S_IWRITE
    assert not locks.set_read_only(path, True)
    assert locks.set_read_only(path, False)
    assert os.stat(path).st_mode & stat.S_IWRITE
