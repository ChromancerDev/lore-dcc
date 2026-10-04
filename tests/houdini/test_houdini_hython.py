"""Runs hython_suite.py inside Houdini. Skipped unless LORE_DCC_HYTHON_TESTS=1."""

from __future__ import annotations

import glob
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUITE = Path(__file__).with_name("hython_suite.py")
PATTERNS = (
    r"C:\Program Files\Side Effects Software\Houdini 22*\bin\hython.exe",
    "/opt/hfs22*/bin/hython",
)


def find_hython() -> Path | None:
    """Return LORE_DCC_HYTHON, else the newest installed Houdini 22 hython, else None."""
    override = os.environ.get("LORE_DCC_HYTHON")
    if override:
        return Path(override) if Path(override).is_file() else None
    matches = sorted(m for pattern in PATTERNS for m in glob.glob(pattern))
    return Path(matches[-1]) if matches else None


@pytest.mark.skipif(
    os.environ.get("LORE_DCC_HYTHON_TESTS") != "1", reason="set LORE_DCC_HYTHON_TESTS=1 to run"
)
def test_hython_suite(tmp_path: Path) -> None:
    hython = find_hython()
    if hython is None:
        pytest.skip("no Houdini 22 hython found")
    env = {
        **os.environ,
        "HOUDINI_PACKAGE_DIR": str(ROOT / "houdini"),
        "HOUDINI_USER_PREF_DIR": str(tmp_path / "prefs__HVER__"),
        "LORE_DCC_TEST_ROOT": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("LORE_DCC_PYTHON", None)
    run = subprocess.run(
        [str(hython), str(SUITE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    sys.stdout.write(run.stdout)
    assert run.returncode == 0, run.stdout + run.stderr
