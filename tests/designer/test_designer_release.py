"""The Designer plugin folder matches what Plugin Manager's installer expects."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import lore_dcc

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "designer"


def test_plugin_info_names_the_shim_package_and_matches_the_core_version() -> None:
    info = json.loads((PLUGIN / "pluginInfo.json").read_text(encoding="utf-8"))
    assert info["metadata_format_version"] == "1"
    assert info["name"] == "lore_dcc_designer"
    assert (PLUGIN / info["name"] / "__init__.py").is_file()
    assert info["version"] == lore_dcc.__version__
    assert info["min_designer_version"] == "16.0.0"


def test_shim_defines_the_entry_points_and_finds_the_core_in_a_checkout() -> None:
    shim = PLUGIN / "lore_dcc_designer" / "__init__.py"
    names = {n.name for n in ast.parse(shim.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)}
    assert {"initializeSDPlugin", "uninitializeSDPlugin"} <= names
    assert (shim.resolve().parents[2] / "python" / "lore_dcc" / "__init__.py").is_file()


def test_release_builds_the_sdplugin_under_its_plugin_name() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "lore_dcc_designer.sdplugin\" lore_dcc_designer -x" in workflow
    assert "lore-dcc-designer-$VERSION.zip" in workflow
    assert "needs: [houdini, blender, designer]" in workflow
