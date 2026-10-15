"""Blender extension shim: put lore_dcc on sys.path and register the add-on.

The Python code is found, in order, at `LORE_DCC_PYTHON`, at `python/` next
to this file (the release zip), or at `../python` (a repository checkout).
"""


def _python_folder():
    import os
    from pathlib import Path

    here = Path(__file__).resolve().parent
    for folder in (os.environ.get("LORE_DCC_PYTHON"), here / "python", here.parent / "python"):
        if folder and (Path(folder) / "lore_dcc" / "__init__.py").is_file():
            return os.path.normpath(folder)
    return None


def register():
    import sys

    folder = _python_folder()
    if folder is None:
        raise ImportError("Lore: the lore_dcc Python folder was not found next to the extension.")
    if folder not in sys.path:
        sys.path.insert(0, folder)
    from lore_dcc.blender import addon

    addon.register(__package__)


def unregister():
    from lore_dcc.blender import addon

    addon.unregister()
