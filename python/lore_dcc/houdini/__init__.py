"""Houdini integration for lore-dcc. Importable only inside Houdini or hython."""
from __future__ import annotations

try:
    import hou  # noqa: F401
except ImportError as exc:
    raise ImportError(
        "lore_dcc.houdini needs Houdini's hou module. Import it inside Houdini or hython."
    ) from exc
