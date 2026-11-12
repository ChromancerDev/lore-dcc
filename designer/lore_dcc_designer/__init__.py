"""Designer plugin entry points. The work lives in `lore_dcc.designer.plugin`.

In a release the `lore_dcc` core sits next to this package. Loaded from a
source checkout, it is found in the checkout's `python/` folder.
"""

import sys
from pathlib import Path

try:
    import lore_dcc  # noqa: F401
except ImportError:
    sys.path.append(str(Path(__file__).resolve().parents[2] / "python"))


def initializeSDPlugin():
    """Called by Designer when the plugin loads."""
    from lore_dcc.designer import plugin

    plugin.initialize()


def uninitializeSDPlugin():
    """Called by Designer when the plugin unloads."""
    from lore_dcc.designer import plugin

    plugin.uninitialize()
