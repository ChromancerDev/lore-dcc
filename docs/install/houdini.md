# Install in Houdini

## Requirements

- Houdini 22 or newer. The package turns itself off in older versions.
- Lore 0.9.0 or newer, with `lore` on `PATH`. Check with `lore --version` in a terminal.
- A Lore working copy, signed in with `lore login`. The plugin never handles credentials.

## Get the files

Download `lore-dcc-houdini-<version>.zip` from the [Releases page](https://github.com/ChromancerDev/lore-dcc/releases). It holds two sibling folders, `python/` and `houdini/`. Keep them side by side: `houdini/lore_dcc.json` finds the Python code at `../python`.

## Just for you

1. Unzip anywhere, for example `C:/tools/lore-dcc/`.
2. Find your Houdini user folder: in Houdini, open `Windows > Python Shell` and run `hou.homeHoudiniDirectory()`. On Windows it is usually `Documents/houdini22.0`. If `HOME` is set (Git Bash sets it), Houdini uses `$HOME/houdini22.0` instead.
3. In that folder, create `packages/lore_dcc_loader.json`:
   ```json
   { "package_path": "C:/tools/lore-dcc/houdini" }
   ```
4. Restart Houdini. A `Lore` menu appears before `Help`. To add the shelf, click `+` on the shelf bar, choose `Shelves`, and tick `Lore`.

## For a team

1. Unzip into the Lore project, for example `<project>/tools/lore-dcc/`, and commit it. Every artist gets it, and every update, on their next `lore sync`.
2. Each artist adds the same loader file as above, pointing at their copy of `<project>/tools/lore-dcc/houdini`. Or, if your launcher already sets environment variables, set `HOUDINI_PACKAGE_DIR` to that folder instead and skip the loader file.

`HOUDINI_PACKAGE_DIR` takes one folder. If your studio already uses it, add a loader file inside that folder rather than replacing it. See [integration.md](../integration.md).

## Check it works

- `Lore > Info` on a saved hip inside a working copy shows the working copy, branch, user and remote.
- On a hip outside a working copy it says `Not a Lore working copy`. That is expected.
- No `Lore` menu at all: check the path in the loader file, then look in Houdini's console for `Lore plugin failed to start`.

## Update

Replace both folders with the ones from the new zip and restart Houdini. On a team copy, commit the new folders.

## Uninstall

Delete `packages/lore_dcc_loader.json` (or unset `HOUDINI_PACKAGE_DIR`) and restart Houdini. Per-user settings are in `lore_dcc_prefs.json` in the Houdini user folder; delete it too for a clean slate.
