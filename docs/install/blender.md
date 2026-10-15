# Install in Blender

## Requirements

- Blender 5.2 or newer. The extension does not install in older versions.
- Lore 0.9.0 or newer, with `lore` on `PATH`. Check with `lore --version` in a terminal.
- A Lore working copy, signed in with `lore login`. The plugin never handles credentials.

## Get the files

Download `lore-dcc-blender-<version>.zip` from the [Releases page](https://github.com/ChromancerDev/lore-dcc/releases). It is a Blender extension: `blender_manifest.toml` at the top, with the plugin's Python code inside it under `python/`. Do not unzip it for a single-artist install.

## Just for you

1. In Blender, open `Edit > Preferences > Get Extensions`.
2. Click the arrow at the top right and choose `Install from Disk...`.
3. Pick `lore-dcc-blender-<version>.zip`.
4. A `Lore` menu appears in the top bar, after `Help`.

Blender copies the extension into its user folder, so you can delete the zip afterwards.

## For a team

Commit the extension to the project so every artist gets it, and every update, on their next `lore sync`.

1. Unzip the zip into a folder named `lore_dcc` inside the project, for example `<project>/tools/blender_extensions/lore_dcc/`. `blender_manifest.toml` must sit directly in `lore_dcc/`. Commit the folder.
2. Each artist adds a local repository once: `Edit > Preferences > Get Extensions`, the arrow at the top right, `Repositories`, `+`, `Add Local Repository`. Tick `Custom Directory` and point it at `<project>/tools/blender_extensions`.
3. Enable `Lore` in the list of installed extensions.

Blender reads the extension straight from the project folder, so an update arrives with `lore sync` and takes effect on the next Blender start.

## Check it works

- `Lore > Info` on a saved .blend inside a working copy shows the working copy, branch, user and remote.
- On a .blend outside a working copy it says `Not a Lore working copy`. That is expected.
- No `Lore` menu: check that the extension is enabled in `Edit > Preferences > Add-ons`, then look in Blender's system console (`Window > Toggle System Console` on Windows) for `Lore` errors.

## Settings

`Edit > Preferences > Add-ons > Lore` holds the per-user toggles: on or off, the lock and stage offers after a save, and the Commit dialog defaults. They are stored in `lore_dcc_prefs.json` in Blender's config folder, for example `%APPDATA%/Blender Foundation/Blender/5.2/config` on Windows.

To lock and collect render outputs on every render of a scene, tick `Lock and collect render outputs` in `Properties > Output > Lore`. The setting is saved in the .blend.

## Update

Single artist: install the new zip the same way; it replaces the old version. Team: replace the `lore_dcc` folder with the new one and commit it.

## Uninstall

`Edit > Preferences > Add-ons`, find `Lore`, and choose `Uninstall` (or remove the local repository on a team install). Delete `lore_dcc_prefs.json` from the config folder for a clean slate.
