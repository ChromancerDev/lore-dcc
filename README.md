# lore-dcc

Lore version control inside DCC applications, in the spirit of SideFX Labs `P4Houdini`. Lock, stage, commit and push from inside the app, with Lore's own words and rules.

**Status:** first release, 0.1.0, for Houdini 22. Blender 5.2 support is new. Tested on Windows. The code is portable, but Linux is untested.

Found a bug? Use `Lore > File a Bug` in Houdini or Blender, or [open an issue](https://github.com/ChromancerDev/lore-dcc/issues/new?template=bug_report.yml).

## Requirements

- Houdini 22, or Blender 5.2
- Lore 0.9.0 or newer, with `lore` on `PATH`
- A Lore working copy. The plugin finds it by walking up from the open file to `.lore/`. It reads the remote and identity from `.lore/config.toml` and needs no other setup.

Sign in with `lore login` in a terminal. The plugin never handles credentials.

## Install

- Houdini: [docs/install/houdini.md](docs/install/houdini.md)
- Blender: [docs/install/blender.md](docs/install/blender.md)
- Hooking it into your own pipeline, scripts and launchers: [docs/integration.md](docs/integration.md)

## Using it

| Where | Actions |
|---|---|
| `Lore` menu in the main menu bar | Info, Lock, Discard changes, Collect Outputs, Sync Dependencies, Commit, Enable/Disable |
| `Lore` shelf | Info, Enable/Disable, Lock, Discard changes, Collect Outputs, Sync Dependencies, Commit |
| Node right-click, `Lore` | Lock and Discard changes on an asset's library file. Lock Outputs and Collect Outputs on ROPs and File Caches. |
| Parameter right-click, `Lore` | Lock and Discard changes on the file a string parameter names |
| Saving | Before saving an unlocked tracked file: "Lore - Lock File?". After saving a new file: "Lore - Stage New File?". Saving over a file someone else locked: "Lore - File Locked by Another User", once per file. Turn the prompts off in `lore_dcc_prefs.json` in your Houdini user folder. |
| After Commit | `Lore - Commit Complete` names the committed revision, the branch it was pushed to and the revision the server is at. |
| Opening a hip | Checks in the background whether any file the scene reads is newer on the server, and offers `Sync Dependencies` when one is. |
| Blender: `Lore` menu in the top bar | Info, Lock, Discard changes, Collect Outputs, Sync Dependencies, Commit, Enable/Disable, File a Bug |
| Blender: file path field right-click | `Lore: Lock` and `Lore: Discard changes` on the file the field names, such as an image, a cache file or a render path |
| Blender: Outliner right-click | `Lore: Lock` and `Lore: Discard changes` on the files of the selected data-blocks that read files, such as images and cache files, in the `Blender File` view |
| Blender: `Properties > Output > Lore` | Lock Outputs, Collect Outputs, and `Lock and collect render outputs` to do both around every render |
| Blender: saving | After saving a tracked file nobody locked: "Lore - Lock File?". After saving a new file: "Lore - Stage New File?". A file someone else locked: "Lore - File Locked by Another User", once per file. Blender cannot wait for an answer before a save, so these come right after it. Turn them off in `Edit > Preferences > Add-ons > Lore`. |
| Blender: opening a .blend | The same background check as for a hip. |

## Dependencies

Every file a scene reads, such as File SOP caches, Alembic and FBX files, textures, USD layers and the HDA libraries it uses, is a reference. When the plugin stages or commits a hip, it records the references inside the working copy as Lore dependency links tagged `houdini`, and removes links the scene no longer uses. New references show in the Commit dialog under `Dependencies` so they commit with the hip. References outside the working copy get a warning, since other users will not have them.

In Blender the references are the files of linked libraries, images (every UDIM tile), sounds, movie clips, Alembic and USD cache files, fonts, volumes and external text files. Packed data and Blender's own bundled files are not references. The links are tagged `blender`, and a .blend counts as lockable. Blender's numbered backups, such as `shot.blend1`, never show in the Commit dialog.

`Sync Dependencies` updates only the references that are newer on the server and reloads them in Houdini. It never touches the open hip unless the hip is behind too and you pick `Sync and reopen`. Files with local changes are skipped and listed.

Lore's scoped sync moves the whole working copy to the server's revision but writes only the files it syncs. Other files that changed on the server keep their old content, `lore status` does not show them, and later syncs do not update them. After that, only `lore file reset <file>` brings such a file up to date, and editing and committing it overwrites the newer version on the server. When you can, run a full `lore sync` outside Houdini instead.

A hip, HDA or cache file that someone else locked cannot be staged or committed, and neither can one whose lock state Lore could not read. To make a ROP lock its own outputs, set its Pre-Render Script and Post-Render Script to Python:

```python
import lore_dcc.houdini.api as lore; lore.pre_render()
```

```python
import lore_dcc.houdini.api as lore; lore.post_render()
```

Pre-render locks the tracked files it is about to write. Post-render stages new files and releases the locks on files that came out byte-identical.

A script that writes files itself, such as an HDA export button or a Python SOP, does the same with two calls. Paths can be absolute or use variables such as `$HIP`, and paths outside every working copy are ignored. A refused lock shows a dialog and raises, so the script stops before it writes.

```python
import lore_dcc.houdini.api as lore
lore.lock(["$HIP/export/hero.fbx", "$HIP/tex/hero_albedo.exr"])
# ... write the files ...
lore.stage(["$HIP/export/hero.fbx", "$HIP/tex/hero_albedo.exr"])
```

In Blender, the same calls live in `lore_dcc.blender.api`, and `Properties > Output > Lore` turns on the render hooks per scene. See [integration.md](docs/integration.md#call-it-from-scripts-blender).

## Per-project settings

An optional `lore-dcc.json` at the root of a Lore working copy changes which file types the plugin locks and which node parameters count as outputs. Commit it like any project file.

```json
{
  "version": 1,
  "lockable": { "add": [".sim"], "remove": [".obj"] },
  "houdini": {
    "outputs": {
      "Sop/filecache": { "parms": ["sopoutput"] },
      "Sop/file": { "parms": ["file"], "when": { "filemode": "write" } }
    }
  }
}
```

- `lockable` adds or removes file suffixes. The defaults are in [`python/lore_dcc/settings.py`](python/lore_dcc/settings.py).
- `houdini.outputs` keys are node type names with an optional `Category/` prefix. Leave out the version: `filecache`, not `filecache::2.0`. An entry replaces the default for that type. `when` limits the entry to nodes whose parameter matches: a number matches the value, a string matches the menu token. The default table is in [`python/lore_dcc/houdini/outputs.py`](python/lore_dcc/houdini/outputs.py).

## Development

```
python -m pip install pytest ruff
python -m pytest -q
ruff check .
```

The core under `python/lore_dcc/` is pure Python 3.13 with no third-party imports. Tests run without Houdini and without a Lore server.

## License

MIT. Lore is developed by Epic Games. Houdini is developed by SideFX. This project is not affiliated with either.
