"""Every popup the plugin can open must carry a `Lore - ` window title."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from lore_dcc.ui import TITLE_PREFIX

SOURCE = Path(__file__).resolve().parents[1] / "python" / "lore_dcc"
# Calls whose first argument is the window title.
TITLED = {"ask", "message", "show_message", "setWindowTitle", "Ask", "Message"}
# Blender calls that open a popup and take its title as `title=`.
BLENDER_POPUPS = {"invoke_props_dialog", "invoke_confirm", "popup_menu"}
# Modules that only forward a caller's title to the UI, where ui.window_title adds the prefix.
FORWARDERS = {"host.py", "messages.py", "tasks.py", "dialogs.py"}


def _calls(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            yield name, node


def _check(path: Path, node: ast.Call, title: ast.expr) -> None:
    if isinstance(title, ast.Constant) and isinstance(title.value, str):
        assert title.value.startswith(TITLE_PREFIX), f"{path.name}:{node.lineno} {title.value!r}"
    else:
        assert path.name in FORWARDERS, f"{path.name}:{node.lineno} title is not a literal"


@pytest.mark.parametrize("path", sorted(SOURCE.rglob("*.py")), ids=lambda p: p.name)
def test_literal_popup_titles_start_with_the_prefix(path: Path) -> None:
    for name, node in _calls(path):
        if name in TITLED and node.args:
            _check(path, node, node.args[0])
        for keyword in node.keywords:
            if keyword.arg == "title" and name in BLENDER_POPUPS:
                _check(path, node, keyword.value)


def test_no_popup_bypasses_the_message_helpers() -> None:
    for path in SOURCE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "hou.ui.display" not in text, path.name
        if path.name != "messages.py":
            assert "QMessageBox(" not in text, path.name
        if path.name not in ("dialogs.py", "commit_ui.py"):
            assert "invoke_props_dialog" not in text, path.name
            assert "popup_menu" not in text, path.name
