"""Houdini startup shim: put lore_dcc on sys.path and register its hooks.

Houdini runs this file from every HOUDINI_PATH entry after HDAs load. Names
are kept inside a function because hook scripts share one namespace.
"""


def _lore_dcc_startup():
    import logging
    import os
    import sys

    import hou

    try:
        folder = hou.getenv("LORE_DCC_PYTHON") or os.environ.get("LORE_DCC_PYTHON")
        if not folder:
            return
        folder = os.path.normpath(folder)
        if folder not in sys.path:
            sys.path.insert(0, folder)
        from lore_dcc.houdini import hooks

        hooks.install()
    except Exception:
        logging.getLogger("lore_dcc").exception("Lore plugin failed to start")


_lore_dcc_startup()
