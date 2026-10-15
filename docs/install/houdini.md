# Install in Houdini

## Requirements

- Houdini 22 or newer. The package turns itself off in older versions.
- Lore 0.9.0 or newer, with `lore` on `PATH`. Check with `lore --version` in a terminal.
- A Lore working copy, signed in with `lore login`. The plugin never handles credentials.

## Install

1. Download `lore-dcc-houdini-<version>.zip` from the [Releases page](https://github.com/ChromancerDev/lore-dcc/releases).
2. Find your Houdini user folder: in Houdini, open `Windows > Python Shell` and run `hou.homeHoudiniDirectory()`.
   - Windows: usually `Documents/houdini22.0`. If `HOME` is set (Git Bash sets it), Houdini uses `$HOME/houdini22.0` instead.
   - Linux: `~/houdini22.0`.
3. Unzip into that folder. You get:
   ```
   houdini22.0/
     packages/lore_dcc_loader.json
     lore-dcc/
       houdini/
       python/
   ```
   If `packages/` already exists, the loader file goes in next to what is there.
4. Restart Houdini. A `Lore` menu appears before `Help`. To add the shelf, click `+` on the shelf bar, choose `Shelves`, and tick `Lore`.

## Check it works

- `Lore > Info` on a saved hip inside a working copy shows the working copy, branch, user and remote.
- On a hip outside a working copy it says `Not a Lore working copy`. That is expected.
- No `Lore` menu at all: check that `packages/lore_dcc_loader.json` and `lore-dcc/` sit directly in the user folder, then look in Houdini's console for `Lore plugin failed to start`.

## Update

Delete the `lore-dcc/` folder, unzip the new release over the user folder, and restart Houdini.

## Uninstall

Delete `packages/lore_dcc_loader.json` and the `lore-dcc/` folder, then restart Houdini. Per-user settings are in `lore_dcc_prefs.json` in the same user folder; delete it too for a clean slate.

To load the plugin from somewhere else, such as a studio tools folder, see [integration.md](../integration.md).
