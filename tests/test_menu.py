"""The Lore main menu XML parses and every item names a real action."""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENU = ROOT / "houdini" / "MainMenuCommon.xml"
SHELF = ROOT / "houdini" / "toolbar" / "lore.shelf"
ACTIONS = ROOT / "python" / "lore_dcc" / "houdini" / "actions.py"


def _called(script: str) -> str:
    """Return the actions function a one-line script calls, checking its import."""
    imports, call = ast.parse(script.strip()).body
    assert isinstance(imports, ast.ImportFrom)
    assert (imports.module, [a.name for a in imports.names]) == ("lore_dcc.houdini", ["actions"])
    assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
    func = call.value.func
    assert isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
    assert func.value.id == "actions" and not call.value.args
    return func.attr


def test_menu_sits_before_help_and_lists_the_actions_in_order() -> None:
    sub = ET.parse(MENU).getroot().find("menuBar/subMenu")
    assert sub is not None
    assert sub.findtext("label") == "Lore"
    assert sub.findtext("insertBefore") == "help_menu"
    items = [(i.findtext("label"), i.findtext("scriptCode")) for i in sub.findall("scriptItem")]
    assert [label for label, _ in items] == [
        "Info", "Lock", "Discard changes", "Collect Outputs", "Sync Dependencies", "Commit",
        "Enable/Disable",
    ]  # fmt: skip
    children = [child.tag for child in sub if child.tag in ("scriptItem", "separatorItem")]
    assert children.index("separatorItem") == 6
    assert [_called(code or "") for _, code in items] == [
        "info", "lock", "discard", "collect_outputs", "sync_dependencies", "commit",
        "toggle_enabled",
    ]  # fmt: skip


def test_every_menu_item_names_a_public_action() -> None:
    defined = {
        node.name
        for node in ast.parse(ACTIONS.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    sub = ET.parse(MENU).getroot().find("menuBar/subMenu")
    assert sub is not None
    for item in sub.findall("scriptItem"):
        assert _called(item.findtext("scriptCode") or "") in defined


def test_menu_and_shelf_offer_the_same_actions() -> None:
    shelf = {
        _called(tool.findtext("script") or "") for tool in ET.parse(SHELF).getroot().iter("tool")
    }
    sub = ET.parse(MENU).getroot().find("menuBar/subMenu")
    assert sub is not None
    menu = {_called(i.findtext("scriptCode") or "") for i in sub.findall("scriptItem")}
    assert menu == shelf
