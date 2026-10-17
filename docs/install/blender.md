# Install in Blender

## Requirements

- Blender 5.2 or newer. The extension does not install in older versions.
- Lore 0.9.0 or newer, with `lore` on `PATH`. Check with `lore --version` in a terminal.
- A Lore working copy, signed in with `lore login`. The plugin never handles credentials.

## Install

1. Download `lore-dcc-blender-<version>.zip` from the [Releases page](https://github.com/ChromancerDev/lore-dcc/releases). Do not unzip it.
2. In Blender, open `Edit > Preferences > Get Extensions`, click the arrow at the top right and choose `Install from Disk...`. Pick the zip. Dragging the zip into the Blender window does the same.
3. A `Lore` menu appears in the top bar, after `Help`.

Blender copies the extension into its user extensions folder, for example `%APPDATA%/Blender Foundation/Blender/5.2/extensions/user_default/lore_dcc` on Windows or `~/.config/blender/5.2/extensions/user_default/lore_dcc` on Linux. You can delete the zip afterwards.

## Check it works

- `Lore > Info` on a saved .blend inside a working copy shows the working copy, branch, user and remote.
- On a .blend outside a working copy it says `Not a Lore working copy`. That is expected.
- No `Lore` menu: check that `Lore` is enabled in `Edit > Preferences > Add-ons`, then look in Blender's system console (`Window > Toggle System Console` on Windows) for `Lore` errors.

## Settings

`Edit > Preferences > Add-ons > Lore` holds the per-user toggles: on or off, the lock and stage offers after a save, and the Commit dialog defaults. They are stored in `lore_dcc_prefs.json` in Blender's config folder, for example `%APPDATA%/Blender Foundation/Blender/5.2/config` on Windows.

To lock and collect render outputs on every render of a scene, tick `Lock and collect render outputs` in `Properties > Output > Lore`. The setting is saved in the .blend.

## Update

Install the new zip the same way. It replaces the old version. Restart Blender.

## Uninstall

In `Edit > Preferences > Add-ons`, open `Lore` and choose `Uninstall`. Delete `lore_dcc_prefs.json` from the config folder for a clean slate.
