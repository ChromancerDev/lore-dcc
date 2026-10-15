"""Register and unregister the whole add-on. `blender/__init__.py` calls these."""

from __future__ import annotations

import logging

import bpy

from lore_dcc.blender import commit_ui, dialogs, hooks, host, operators

logger = logging.getLogger(__name__)

_registered: list[type] = []


def register(package: str) -> None:
    """Register classes, menus, handlers and the timer.

    Args:
        package: The extension's module name, which names its preferences.
    """
    classes = [
        *dialogs.CLASSES,
        *commit_ui.CLASSES,
        *operators.CLASSES,
        operators.preferences_class(package),
    ]
    for cls in classes:
        bpy.utils.register_class(cls)
        _registered.append(cls)
    commit_ui.register()
    bpy.types.Scene.lore = bpy.props.PointerProperty(type=operators.LoreSceneSettings)
    for menu, draw in operators.MENU_HOOKS:
        getattr(bpy.types, menu).append(draw)
    hooks.install()
    host.install_timer()


def unregister() -> None:
    """Undo register. Safe after a partial register."""
    host.remove_timer()
    host.shutdown()
    hooks.remove()
    for menu, draw in operators.MENU_HOOKS:
        menu_type = getattr(bpy.types, menu, None)
        if menu_type is not None:
            try:
                menu_type.remove(draw)
            except ValueError:
                pass
    if hasattr(bpy.types.Scene, "lore"):
        del bpy.types.Scene.lore
    commit_ui.unregister()
    while _registered:
        cls = _registered.pop()
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            logger.warning("Lore could not unregister %s", cls.__name__)
