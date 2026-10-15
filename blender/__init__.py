"""Blender extension entry point for the Lore add-on.

The lore_dcc core ships as the wheel that blender_manifest.toml lists, and
Blender installs it when the extension is installed.
"""


def register():
    from lore_dcc.blender import addon

    addon.register(__package__)


def unregister():
    from lore_dcc.blender import addon

    addon.unregister()
