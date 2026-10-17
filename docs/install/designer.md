# Install in Substance 3D Designer

## Requirements

- Substance 3D Designer 16.0 or newer. Plugin Manager refuses the plugin in older versions.
- Lore 0.9.0 or newer, with `lore` on `PATH`. Check with `lore --version` in a terminal.
- A Lore working copy, signed in with `lore login`. The plugin never handles credentials.

## Install

1. Download `lore-dcc-designer-<version>.zip` from the [Releases page](https://github.com/ChromancerDev/lore-dcc/releases) and unzip it anywhere. It holds `lore_dcc_designer.sdplugin`.
2. In Designer, open `Tools > Plugin Manager` and click `INSTALL...`. Pick `lore_dcc_designer.sdplugin`. Keep its file name: Designer reads the plugin name from it.
3. A `Lore` menu appears in the main menu bar.

Designer unpacks the plugin into its user plugins folder, `Documents/Adobe/Adobe Substance 3D Designer/python/sduserplugins/lore_dcc_designer` on Windows, and loads it at every start. You can delete the zip and the `.sdplugin` afterwards.

## Check it works

- Open a graph of a saved package inside a working copy and choose `Lore > Info`. It shows the working copy, branch, user and remote.
- On a package outside a working copy it says `Not a Lore working copy`. That is expected.
- No `Lore` menu: check that `lore_dcc_designer` is listed and ticked in `Tools > Plugin Manager`, then look in `Windows > Console` for `Lore` errors.

## Settings

Per-user toggles live in `lore_dcc_prefs.json` in `Documents/Adobe/Adobe Substance 3D Designer`: on or off, the lock offer before a save, the stage offer after a save, and the Commit dialog defaults. `Lore > Enable/Disable` switches the plugin on or off.

## Update

Plugin Manager cannot replace an installed plugin.

1. Close Designer.
2. Delete the `lore_dcc_designer` folder from `Documents/Adobe/Adobe Substance 3D Designer/python/sduserplugins`.
3. Start Designer and install the new `.sdplugin` as above.

Your settings stay, since they live outside the plugin folder.

## Uninstall

Close Designer and delete the `lore_dcc_designer` folder from the user plugins folder. Delete `lore_dcc_prefs.json` too for a clean slate.
