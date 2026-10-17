"""Start and stop the plugin: the Lore menu, the Explorer toolbar buttons and the package hooks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from lore_dcc.designer import actions, hooks, host

logger = logging.getLogger(__name__)

MENU_OBJECT = "lore_dcc_menu"

# None is a separator.
MENU: tuple[tuple[str, Callable[[], None]] | None, ...] = (
    ("Info", actions.info),
    ("Lock", actions.lock),
    ("Discard changes", actions.discard),
    ("Sync Dependencies", actions.sync_dependencies),
    ("Commit", actions.commit),
    None,
    ("Enable/Disable", actions.toggle_enabled),
    None,
    ("File a Bug", actions.file_a_bug),
)

EXPLORER: tuple[tuple[str, str, Callable[[int], None]], ...] = (
    (
        "Lore: Lock",
        (
            "Lock the selected package or linked resource in Lore, or the current package. "
            "Perforce calls this a checkout."
        ),
        actions.lock_selection,
    ),
    (
        "Lore: Discard changes",
        "Discard changes to the selected package or linked resource. Perforce calls this a revert.",
        actions.discard_selection,
    ),
    (
        "Lore: Commit",
        "Commit and push the current package. Perforce calls this a submit.",
        lambda _explorer_id: actions.commit(),
    ),
)

_ui_callback_ids: list[int] = []
_keep: list[Any] = []


def initialize() -> None:
    """Install the hooks, the menu and the Explorer buttons. Never raises."""
    try:
        host.setup()
    except Exception:
        logger.exception("Lore could not set up its dialogs")
    hooks.install()
    ui = host.qt_ui()
    if ui is None:
        return
    try:
        _build_menu(ui)
        _ui_callback_ids.append(ui.registerExplorerCreatedCallback(_on_explorer_created))
    except Exception:
        logger.exception("Lore menu could not be installed")


def uninitialize() -> None:
    """Remove everything `initialize` added."""
    hooks.uninstall()
    ui = host.qt_ui()
    if ui is None:
        return
    while _ui_callback_ids:
        try:
            ui.unregisterCallback(_ui_callback_ids.pop())
        except Exception:
            logger.exception("Lore could not unregister a callback")
    try:
        ui.deleteMenu(MENU_OBJECT)
    except Exception:
        logger.exception("Lore menu could not be removed")
    _keep.clear()


def _build_menu(ui: Any) -> None:
    if ui.findMenuFromObjectName(MENU_OBJECT) is not None:
        ui.deleteMenu(MENU_OBJECT)
    menu = ui.newMenu("Lore", MENU_OBJECT)
    for entry in MENU:
        if entry is None:
            menu.addSeparator()
            continue
        label, action = entry
        item = menu.addAction(label)
        item.triggered.connect(lambda *_args, run=action: run())


def _on_explorer_created(explorer_id: int) -> None:
    try:
        from PySide6 import QtGui

        ui = host.qt_ui()
        for label, tip, action in EXPLORER:
            item = QtGui.QAction(label, ui.getMainWindow())
            item.setToolTip(tip)
            item.triggered.connect(lambda *_args, run=action, eid=explorer_id: run(eid))
            _keep.append(item)
            ui.addActionToExplorerToolbar(explorer_id, item)
    except Exception:
        logger.exception("Lore could not add its Explorer buttons")
