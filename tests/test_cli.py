"""LoreCli tests with a fake subprocess runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lore_dcc import locks as locks_module
from lore_dcc.backend import cli
from lore_dcc.errors import (
    LockNotAcquired,
    LoreCommandError,
    LoreOutputError,
    LoreVersionError,
)

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
        make(tmp_path, runner).push()
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


FILE_INFO_BLOCK = (
    "Path:    {path}\nType:    file\nSize:    1\nMode:    000\n"
    f"Context: {'0' * 32}\nHash:    {'0' * 64}\nStatus:  -\n"
)


def test_file_info_runs_absolute_paths_and_merges_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "BATCH_SIZE", 1)
    runner = FakeRunner(
        (0, FILE_INFO_BLOCK.format(path="b.hip")),
        (0, ""),
    )
    infos = make(tmp_path, runner).file_info([Path("b.hip"), Path("missing.hip")])
    assert list(infos) == ["b.hip"]
    assert runner.calls[0] == [
        "lore", "-P", "--non-interactive", "file", "info", str(tmp_path.resolve() / "b.hip"),
    ]  # fmt: skip
    assert len(runner.calls) == 2


def test_file_info_empty_paths_runs_nothing(tmp_path: Path) -> None:
    runner = FakeRunner()
    assert make(tmp_path, runner).file_info([]) == {}
    assert runner.calls == []


def test_file_info_unknown_output_raises(tmp_path: Path) -> None:
    runner = FakeRunner((0, FILE_INFO_BLOCK.format(path="a.hip").replace("-", "Weird")))
    with pytest.raises(LoreOutputError):
        make(tmp_path, runner).file_info([Path("a.hip")])


def test_branch_info_runs_branch_info(tmp_path: Path) -> None:
    runner = FakeRunner((0, "Branch main\n  ID: ab12\n"))
    info = make(tmp_path, runner).branch_info()
    assert info.id == "ab12"
    assert runner.calls == [["lore", "-P", "--non-interactive", "branch", "info"]]


def test_branch_info_unknown_output_raises(tmp_path: Path) -> None:
    runner = FakeRunner((0, "Branch main\n  ID: ab12\n  Colour: red\n"))
    with pytest.raises(LoreOutputError):
        make(tmp_path, runner).branch_info()


def test_lock_acquire_held_check_is_case_insensitive_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(locks_module, "CASE_INSENSITIVE", True)
    runner = FakeRunner((0, "Lock acquired on files:\nscenes/A.HIP\n"))
    make(tmp_path, runner).lock_acquire([Path("scenes/a.hip")])


def test_lock_acquire_held_check_is_exact_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(locks_module, "CASE_INSENSITIVE", False)
    runner = FakeRunner((0, "Lock acquired on files:\nscenes/A.HIP\n"))
    with pytest.raises(LockNotAcquired):
        make(tmp_path, runner).lock_acquire([Path("scenes/a.hip")])


def test_revision_status_asks_for_revision_only_and_parses_the_remote(tmp_path: Path) -> None:
    out = (
        f"Repository 0123\nOn branch main revision 3 -> {SIG}\n"
        f"Remote revision 5 -> {SIG}\nLocal branch is behind remote\n"
    )
    runner = FakeRunner((0, out))
    status = make(tmp_path, runner).revision_status()
    assert runner.calls == [["lore", "-P", "--non-interactive", "status", "--revision-only"]]
    assert (status.revision, status.remote_revision) == (3, 5)


def test_dependency_add_and_remove_pass_one_tag_value_then_absolute_paths(tmp_path: Path) -> None:
    runner = FakeRunner()
    lore = make(tmp_path, runner)
    lore.dependency_add(Path("s.hip"), [Path("geo/a.bgeo")])
    lore.dependency_remove(Path("s.hip"), [Path("geo/b.bgeo")])
    lore.dependency_add(Path("s.hip"), [])
    root = tmp_path.resolve()
    head = ["lore", "-P", "--non-interactive", "file", "dependency"]
    assert runner.calls == [
        [*head, "add", "--tag=houdini", str(root / "s.hip"), str(root / "geo/a.bgeo")],
        [*head, "remove", "--tag=houdini", str(root / "s.hip"), str(root / "geo/b.bgeo")],
    ]


def test_dependency_list_parses_the_edges(tmp_path: Path) -> None:
    out = f"{tmp_path}/s.hip:\n  geo/a.bgeo [houdini]\n"
    assert make(tmp_path, FakeRunner((0, out))).dependency_list(Path("s.hip")) == ("geo/a.bgeo",)


def test_revision_diff_passes_signatures(tmp_path: Path) -> None:
    runner = FakeRunner((0, "M geo/a.bgeo\n"))
    changes = make(tmp_path, runner).revision_diff("a" * 64, "b" * 64)
    assert runner.calls[0][3:] == ["revision", "diff", "a" * 64, "--target", "b" * 64]
    assert [(c.code, c.path) for c in changes] == [("M", "geo/a.bgeo")]


def test_sync_roots_passes_relative_roots_in_one_call(tmp_path: Path) -> None:
    runner = FakeRunner()
    make(tmp_path, runner).sync_roots(["geo/a.bgeo", "tex/b.exr"], dry_run=True)
    assert runner.calls == [
        [
            "lore", "-P", "--non-interactive", "sync",
            "--root-file", "geo/a.bgeo", "--root-file", "tex/b.exr", "--dependency-tag", cli.ROOTS_ONLY_TAG, "--dry-run",
        ]
    ]  # fmt: skip
    assert runner.kwargs[0]["timeout"] == cli.SYNC_TIMEOUT


def test_sync_roots_refuses_empty_and_oversized(tmp_path: Path) -> None:
    lore = make(tmp_path, FakeRunner())
    with pytest.raises(ValueError):
        lore.sync_roots([])
    with pytest.raises(ValueError):
        lore.sync_roots(["x" * 100] * 1000)
