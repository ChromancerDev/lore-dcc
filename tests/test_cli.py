"""LoreCli tests with a fake subprocess runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_dcc.backend import cli
from lore_dcc.errors import LockNotAcquired, LoreCommandError, LoreVersionError

SIG = "9" * 64


class FakeRunner:
    """Records argv and answers from a queue of (returncode, stdout)."""

    def __init__(self, *answers: tuple[int, str]) -> None:
        self.answers = list(answers)
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        self.kwargs.append(kwargs)
        code, out = self.answers.pop(0) if self.answers else (0, "")
        return subprocess.CompletedProcess(argv, code, out, "")


def make(tmp_path: Path, runner: FakeRunner) -> cli.LoreCli:
    return cli.LoreCli(tmp_path, executable="lore", runner=runner)


def test_global_flags_cwd_and_absolute_paths(tmp_path: Path) -> None:
    runner = FakeRunner()
    make(tmp_path, runner).stage([Path("scenes/shot.hip")])
    assert runner.calls == [
        ["lore", "-P", "--non-interactive", "stage", str(tmp_path.resolve() / "scenes/shot.hip")]
    ]
    assert runner.kwargs[0]["cwd"] == tmp_path.resolve()
    assert runner.kwargs[0]["stdin"] is subprocess.DEVNULL


def test_batches_by_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "BATCH_CHARS", 10**9)
    runner = FakeRunner()
    paths = [Path(f"f{i}.bgeo.sc") for i in range(cli.BATCH_SIZE + 1)]
    make(tmp_path, runner).dirty(paths)
    assert [len(call) - 4 for call in runner.calls] == [cli.BATCH_SIZE, 1]


def test_lock_acquire_checks_every_path(tmp_path: Path) -> None:
    runner = FakeRunner(
        (0, "Lock acquired on files:\nscenes/a.hip\n"),
    )
    make(tmp_path, runner).lock_acquire([Path("scenes/a.hip")])
    assert runner.calls[0][3:5] == ["lock", "acquire"]


def test_lock_acquire_missing_path_raises(tmp_path: Path) -> None:
    runner = FakeRunner((0, "Lock acquired on files:\nscenes/a.hip\n"))
    with pytest.raises(LockNotAcquired) as info:
        make(tmp_path, runner).lock_acquire([Path("scenes/a.hip"), Path("scenes/b.hip")])
    assert info.value.paths == ["scenes/b.hip"]


def test_batches_by_length(tmp_path: Path) -> None:
    runner = FakeRunner()
    long_name = "x" * 200
    paths = [Path(f"{long_name}{i}.bgeo.sc") for i in range(200)]
    make(tmp_path, runner).dirty(paths)
    assert len(runner.calls) > 1
    for call in runner.calls:
        assert sum(len(arg) + 3 for arg in call[4:]) <= cli.BATCH_CHARS


def test_status_merges_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "BATCH_SIZE", 1)
    head = f"On branch main revision 3 -> {SIG}\n"
    runner = FakeRunner(
        (0, head + "Changes not staged for commit:\nM a.hip\n"),
        (0, head + "Changes not staged for commit:\nM b.hip\n"),
    )
    paths = [Path("a.hip"), Path("b.hip")]
    status = make(tmp_path, runner).check_dirty(paths)
    assert len(runner.calls) == 2
    assert runner.calls[0][3:5] == ["status", "--check-dirty"]
    assert [c.path for c in status.unstaged] == ["a.hip", "b.hip"]


def test_nonzero_exit_raises_with_output(tmp_path: Path) -> None:
    runner = FakeRunner((6, "[Error] Disconnected from server\n"))
    with pytest.raises(LoreCommandError) as info:
        make(tmp_path, runner).push("main")
    assert info.value.returncode == 6
    assert "Disconnected" in info.value.output


def test_timeout_raises(tmp_path: Path) -> None:
    def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(argv, 1.0, output=b"partial")

    lore = cli.LoreCli(tmp_path, executable="lore", runner=runner)
    with pytest.raises(LoreCommandError) as info:
        lore.lock_query()
    assert info.value.returncode is None
    assert info.value.output == "partial"


def test_version_floor(tmp_path: Path) -> None:
    runner = FakeRunner((0, "lore 0.8.6+100\n"))
    with pytest.raises(LoreVersionError):
        make(tmp_path, runner).version()
    assert runner.calls[0] == ["lore", "--version"]


def test_version_newer_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    runner = FakeRunner((0, "lore 0.10.0+1\n"))
    assert make(tmp_path, runner).version() == (0, 10, 0)
    assert "newer than the tested" in caplog.text


def test_missing_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(cli.LoreNotFound):
        cli.LoreCli(tmp_path)
