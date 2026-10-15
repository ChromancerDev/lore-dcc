"""The Houdini zip unzips into the Houdini user folder and its packages chain together."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_loader_points_from_packages_to_the_lore_dcc_folder() -> None:
    loader = json.loads((ROOT / "release/houdini/packages/lore_dcc_loader.json").read_text())
    assert loader == {"package_path": "$HOUDINI_PACKAGE_PATH/../lore-dcc/houdini"}


def test_package_finds_python_next_to_itself() -> None:
    package = json.loads((ROOT / "houdini/lore_dcc.json").read_text())
    assert {"LORE_DCC_PYTHON": "$HOUDINI_PACKAGE_PATH/../python"} in package["env"]
