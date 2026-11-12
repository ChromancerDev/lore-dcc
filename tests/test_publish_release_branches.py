"""publish-release-branches.sh writes each release zip onto release/<dcc>, with .sdplugin files unpacked."""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/publish-release-branches.sh"

pytestmark = pytest.mark.skipif(
    not (shutil.which("bash") and shutil.which("unzip")), reason="needs bash and unzip"
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _publish(tmp_path: Path, version: str) -> Path:
    origin = tmp_path / "origin.git"
    if not origin.exists():
        _git(tmp_path, "init", "--bare", "-q", str(origin))
    clone = tmp_path / "clone"
    if not clone.exists():
        _git(tmp_path, "clone", "-q", str(origin), str(clone))
        _git(clone, "config", "user.name", "Test")
        _git(clone, "config", "user.email", "test@example.com")
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    plugin = dist / "lore_dcc_designer.sdplugin"
    with zipfile.ZipFile(plugin, "w") as zf:
        zf.writestr("lore_dcc_designer/pluginInfo.json", "{}\n")
        zf.writestr("lore_dcc_designer/lore_dcc/__init__.py", "")
    with zipfile.ZipFile(dist / f"lore-dcc-designer-{version}.zip", "w") as zf:
        zf.write(plugin, plugin.name)
        zf.writestr("LICENSE", "MIT\n")
    plugin.unlink()
    bash = shutil.which("bash")
    env = {**os.environ, "PATH": os.pathsep.join([str(Path(bash).parent), os.environ["PATH"]])}
    result = subprocess.run(
        [bash, SCRIPT.as_posix(), version, dist.as_posix()],
        cwd=clone, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return clone


def _tree(clone: Path, ref: str) -> list[str]:
    _git(clone, "fetch", "-q", "origin", "+refs/heads/release/*:refs/remotes/origin/release/*")
    return _git(clone, "ls-tree", "-r", "--name-only", ref).splitlines()


def test_designer_branch_holds_the_unpacked_plugin(tmp_path: Path) -> None:
    clone = _publish(tmp_path, "9.9.9")
    assert sorted(_tree(clone, "origin/release/designer")) == [
        "LICENSE",
        "VERSION",
        "lore_dcc_designer/lore_dcc/__init__.py",
        "lore_dcc_designer/pluginInfo.json",
    ]


def test_rerun_for_the_same_version_adds_nothing(tmp_path: Path) -> None:
    clone = _publish(tmp_path, "9.9.9")
    first = _git(clone, "rev-parse", "origin/release/designer")
    _publish(tmp_path, "9.9.9")
    _tree(clone, "origin/release/designer")
    assert _git(clone, "rev-parse", "origin/release/designer") == first
