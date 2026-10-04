"""Give pure-Python tests a stand-in `hou` module outside Houdini."""

from __future__ import annotations

import sys
import types

try:
    import hou  # noqa: F401
except ImportError:
    stub = types.ModuleType("hou")
    stub.Error = RuntimeError
    stub.frame = lambda: 1.0
    sys.modules["hou"] = stub
