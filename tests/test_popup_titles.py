"""Every popup the plugin can open must carry a `Lore - ` window title."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from lore_dcc.ui import TITLE_PREFIX

SOURCE = Path(__file__).resolve().parents[1] / "python" / "lore_dcc"
# Calls whose first argument is the window title.
TITLED = {"ask", "message", "show_message", "setWindowTitle"}
# Modules that only forward a caller's title to Qt, where ui.window_title adds the prefix.
FORWARDERS = {"host.py", "messages.py", "tasks.py"}


def _calls(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            yield name, node


@pytest.mark.parametrize("path", sorted(SOURCE.rglob("*.py")), ids=lambda p: p.name)
def test_literal_popup_titles_start_with_the_prefix(path: Path) -> None:
    for name, node in _calls(path):
        if name not in TITLED:
            continue
        title = node.args[0]
        if isinstance(title, ast.Constant) and isinstance(title.value, str):
            assert title.value.startswith(TITLE_PREFIX), f"{path.name}:{node.lineno} {title.value!r}"
        else:
            assert path.name in FORWARDERS, f"{path.name}:{node.lineno} title is not a literal"


def test_no_popup_bypasses_the_message_helpers() -> None:
    for path in SOURCE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "hou.ui.display" not in text, path.name
        if path.name != "messages.py":
            assert "QMessageBox(" not in text, path.name
