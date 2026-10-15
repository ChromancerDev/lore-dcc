# Integrate with your own tools

The plugin is plain Python with no third-party packages. Your tools can load it alongside their own, call it from scripts, and change its defaults per project.

## Load it next to a studio package (Houdini)

The plugin is a normal Houdini package: `houdini/lore_dcc.json`. It sets one variable, `LORE_DCC_PYTHON`, which `houdini/python3.13libs/ready.py` puts on `sys.path` at startup. Nothing else touches your environment.

Pick whichever fits your setup:

- **Studio package folder.** Drop a loader file into the folder your studio already lists in `HOUDINI_PACKAGE_DIR`:
  ```json
  { "package_path": "$PROJECT_ROOT/tools/lore-dcc/houdini" }
  ```
  Any variable your launcher sets works in the path.
- **From your own package.** Add the plugin's folder to `package_path` in a package you already ship, so one entry loads both.
- **Launcher.** Set `HOUDINI_PACKAGE_DIR` to `<lore-dcc>/houdini` when nothing else uses that variable.

Leave `python/` and `houdini/` side by side. To keep them apart, set `LORE_DCC_PYTHON` to the `python/` folder yourself before Houdini starts.

## Call it from scripts (Houdini)

`lore_dcc.houdini.api` is the supported surface for scripts.

**ROP and File Cache nodes.** Set the Pre-Render and Post-Render scripts to Python:

```python
import lore_dcc.houdini.api as lore; lore.pre_render()
```

```python
import lore_dcc.houdini.api as lore; lore.post_render()
```

Pre-render locks the tracked files the node is about to write, for its frame range. Post-render stages new files and releases locks on files that came out byte-identical. A refusal raises, after a dialog in a GUI session, so the render stops before it writes over a file someone else locked. A post-render failure shows a dialog in a GUI session and raises only in hython and batch renders.

**Exporters, HDA buttons, Python SOPs.** Wrap the write with `lock` and `stage`:

```python
import lore_dcc.houdini.api as lore

files = ["$HIP/export/hero.fbx", "$HIP/tex/hero_albedo.exr"]
lore.lock(files)    # raises if someone else holds a lock
# ... write the files ...
lore.stage(files)   # stages new files, releases locks on unchanged ones
```

Paths may be absolute or use Houdini variables. Paths outside every working copy are ignored, so the same tool works in and out of Lore projects.

## Call it from scripts (Blender)

`lore_dcc.blender.api` is the supported surface for scripts. The extension ships the `lore_dcc` core as a Python wheel that Blender installs with it, so `import lore_dcc` works in any script once the extension is installed.

**Exporters, bake scripts, batch tools.** Wrap the write with `lock` and `stage`:

```python
import lore_dcc.blender.api as lore

files = ["//export/hero.fbx", "//tex/hero_albedo.exr"]
lore.lock(files)    # raises if someone else holds a lock
# ... write the files ...
lore.stage(files)   # stages new files, releases locks on unchanged ones
```

Paths may be absolute or start with `//`, relative to the open .blend. Paths outside every working copy are ignored. These calls block: they wait on the calling thread until any other Lore call has finished and then until their own has, since a script must not write before its lock is held. Called from Blender's UI thread, Blender freezes for that long. They share one lock with the add-on's own Lore calls, so they never slip into a commit that is running. In the UI a refusal also shows a popup.

**Renders.** `lore.pre_render(scene)` locks the tracked frames and compositor File Output images a render of `scene` will write. `lore.post_render(scene)` stages the new ones and releases locks on the ones that came out byte-identical. Ticking `Lock and collect render outputs` in `Properties > Output > Lore` runs both from Blender's render handlers. A refused `pre_render()` raises, after a popup in the UI. Blender ignores errors raised in render handlers, so the toggle cannot stop an F12 or Ctrl+F12 render that has started: the popup shows (or the console prints it in `blender -b`) and the render goes on. When a refusal must stop the render, call `lore.pre_render()` in your render script before `bpy.ops.render.render()`.

## Call the core from any Python

Outside a DCC, for example in a publish script or a farm job, use the core directly. It needs Python 3.13 and `lore` on `PATH`.

```python
import sys
sys.path.insert(0, "<lore-dcc>/python")

from lore_dcc.session import session_for

path = "D:/projects/mygame/geo/rock.bgeo"
session = session_for(path)          # finds the working copy from the file
session.prepare_write([path])        # locks it if tracked, raises if someone else holds it
# ... write ...
result = session.finish_write([path])  # stages new files, releases unchanged locks
```

`session_for` raises `NotAWorkingCopy` for a file outside a working copy and `LoreNotFound` when `lore` is not on `PATH`. Every failure the plugin raises is a subclass of `lore_dcc.errors.LoreError`. Modules under `lore_dcc.houdini` and `lore_dcc.ui` need Houdini, and `lore_dcc.blender` needs Blender. They are not for standalone use.

## Per-project settings

Commit an optional `lore-dcc.json` at the root of the working copy to change which file types lock and which Houdini node parameters count as outputs. The format is in the [README](../README.md#per-project-settings).

## Per-user settings

`lore_dcc_prefs.json` in the Houdini user folder, or in Blender's config folder, holds each artist's toggles. A studio can pre-seed it:

```json
{
  "version": 1,
  "enabled": true,
  "prompt_lock_before_save": true,
  "prompt_stage_after_save": true,
  "push_after_commit": true,
  "keep_files_locked": false
}
```

## Bug reports from your tools

`lore_dcc.bugs.report_url(dcc, dcc_version)` returns the prefilled GitHub bug form link, if your own help menu wants to point at it.
