"""Blender operators, the Lore menu, context menu entries and the Output panel.

Each operator starts one flow from `actions` and returns at once; the flow
reports through popups. Labels use Lore words, tooltips may name the
Perforce equivalent.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import bpy

from lore_dcc.blender import actions, host, references
from lore_dcc.blender.flow import Flow


def _operator(idname: str, label: str, tooltip: str, action: Callable[[], Flow]) -> type:
    def execute(self, context):
        host.start(action())
        return {"FINISHED"}

    name = "LORE_OT_" + idname.split(".")[1]
    return type(
        name,
        (bpy.types.Operator,),
        {"bl_idname": idname, "bl_label": label, "bl_description": tooltip, "execute": execute},
    )


# idname, label, tooltip, action; the Lore menu shows them in this order.
MENU_ITEMS = (
    ("lore.info", "Info", "Show the working copy, branch, user and remote", actions.info),
    (
        "lore.lock",
        "Lock",
        (
            "Lock the open file so nobody else changes it. A new file is also staged. "
            "Perforce: check out, or add"
        ),
        actions.lock,
    ),
    (
        "lore.discard",
        "Discard changes",
        (
            "Throw away your changes to the open file and reopen the committed version. "
            "Perforce: revert"
        ),
        actions.discard,
    ),
    (
        "lore.collect_outputs",
        "Collect Outputs",
        (
            "Report the files this scene writes (render frames, File Output images, bakes) to Lore: "
            "stage new ones, release the locks on unchanged ones"
        ),
        actions.collect_outputs,
    ),
    (
        "lore.sync_dependencies",
        "Sync Dependencies",
        (
            "Get the newer server versions of the files this scene reads. Perforce: get latest "
            "on the scene's files"
        ),
        actions.sync_dependencies,
    ),
    (
        "lore.commit",
        "Commit",
        "Commit and push your changes. Perforce: submit",
        actions.commit,
    ),
    None,
    ("lore.toggle_enabled", "Enable/Disable", "Turn Lore on or off for you", actions.toggle_enabled),
    None,
    ("lore.file_a_bug", "File a Bug", "Open a bug report for this plugin on GitHub", actions.file_a_bug),
)

MENU_OPERATORS = tuple(_operator(*item) for item in MENU_ITEMS if item is not None)

LOCK_OUTPUTS = _operator(
    "lore.lock_outputs",
    "Lock Outputs",
    "Lock the tracked files this scene's render is about to write. Perforce: check out",
    actions.lock_outputs,
)


class LORE_MT_main(bpy.types.Menu):
    """Lore version control"""

    bl_idname = "LORE_MT_main"
    bl_label = "Lore"

    def draw(self, context):
        layout = self.layout
        enabled = host.load_prefs().enabled
        for item in MENU_ITEMS:
            if item is None:
                layout.separator()
                continue
            idname, label = item[0], item[1]
            if idname == "lore.toggle_enabled":
                icon = "CHECKBOX_HLT" if enabled else "CHECKBOX_DEHLT"
                layout.operator(idname, text=label, icon=icon)
            else:
                layout.operator(idname, text=label)


def draw_topbar(self, context) -> None:
    """Add the Lore menu to the top bar."""
    self.layout.menu(LORE_MT_main.bl_idname)


class LORE_OT_lock_path(bpy.types.Operator):
    """Lock this file so nobody else changes it. A new file is also staged. Perforce: check out"""

    bl_idname = "lore.lock_path"
    bl_label = "Lock"
    bl_options = {"INTERNAL"}  # noqa: RUF012 - Blender reads a set

    filepath: bpy.props.StringProperty(options={"SKIP_SAVE"})  # type: ignore[valid-type]

    def execute(self, context):
        host.start(actions.lock_files([self.filepath]))
        return {"FINISHED"}


class LORE_OT_discard_path(bpy.types.Operator):
    """Throw away your changes to this file and reload the committed version. Perforce: revert"""

    bl_idname = "lore.discard_path"
    bl_label = "Discard changes"
    bl_options = {"INTERNAL"}  # noqa: RUF012 - Blender reads a set

    filepath: bpy.props.StringProperty(options={"SKIP_SAVE"})  # type: ignore[valid-type]

    def execute(self, context):
        host.start(actions.discard_files([self.filepath]))
        return {"FINISHED"}


def selected_files(context: Any) -> list[str]:
    """Return the files of the data-blocks selected in the Outliner, in selection order."""
    files: list[str] = []
    for block in getattr(context, "selected_ids", None) or ():
        files += references.files_of(block)
    return list(dict.fromkeys(files))


class LORE_OT_lock_selected(bpy.types.Operator):
    """Lock the files of the selected libraries, images and caches. Perforce: check out"""

    bl_idname = "lore.lock_selected"
    bl_label = "Lock"

    @classmethod
    def poll(cls, context):
        return bool(selected_files(context))

    def execute(self, context):
        host.start(actions.lock_files(selected_files(context)))
        return {"FINISHED"}


class LORE_OT_discard_selected(bpy.types.Operator):
    """Throw away your changes to the selected files and reload them. Perforce: revert"""

    bl_idname = "lore.discard_selected"
    bl_label = "Discard changes"

    @classmethod
    def poll(cls, context):
        return bool(selected_files(context))

    def execute(self, context):
        host.start(actions.discard_files(selected_files(context)))
        return {"FINISHED"}


def button_file(context: Any) -> str | None:
    """Return the absolute file a right-clicked file path field names, or None."""
    prop = getattr(context, "button_prop", None)
    owner = getattr(context, "button_pointer", None)
    if prop is None or owner is None:
        return None
    if getattr(prop, "type", "") != "STRING" or getattr(prop, "subtype", "") != "FILE_PATH":
        return None
    value = getattr(owner, prop.identifier, "")
    if not isinstance(value, str) or not value.strip():
        return None
    library = getattr(getattr(owner, "id_data", None), "library", None)
    return os.path.normpath(bpy.path.abspath(value, library=library))


def draw_button_menu(self, context) -> None:
    """Add Lock and Discard changes to the right-click menu of a file path field."""
    file = button_file(context)
    if not file:
        return
    layout = self.layout
    layout.separator()
    layout.operator(LORE_OT_lock_path.bl_idname, text="Lore: Lock").filepath = file
    layout.operator(LORE_OT_discard_path.bl_idname, text="Lore: Discard changes").filepath = file


def draw_outliner_menu(self, context) -> None:
    """Add Lock and Discard changes to the Outliner's right-click menu for file data-blocks."""
    if not selected_files(context):
        return
    layout = self.layout
    layout.separator()
    layout.operator(LORE_OT_lock_selected.bl_idname, text="Lore: Lock")
    layout.operator(LORE_OT_discard_selected.bl_idname, text="Lore: Discard changes")


class LoreSceneSettings(bpy.types.PropertyGroup):
    """Per-scene Lore settings, saved in the .blend."""

    render_outputs: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Lock and collect render outputs",
        description=(
            "Before a render, lock the tracked frames and File Output images it writes. "
            "After it, stage new files and release the locks on unchanged ones"
        ),
    )


class LORE_PT_outputs(bpy.types.Panel):
    """Lore controls in the Output properties"""

    bl_idname = "LORE_PT_outputs"
    bl_label = "Lore"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "output"
    bl_options = {"DEFAULT_CLOSED"}  # noqa: RUF012 - Blender reads a set

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene.lore, "render_outputs")
        row = layout.row()
        row.operator(LOCK_OUTPUTS.bl_idname)
        row.operator("lore.collect_outputs")


def _pref_property(name: str, label: str, tooltip: str) -> Any:
    def get(self) -> bool:
        return bool(getattr(host.load_prefs(), name))

    def set_(self, value: bool) -> None:
        import dataclasses

        host.save_prefs(dataclasses.replace(host.load_prefs(), **{name: bool(value)}))

    return bpy.props.BoolProperty(name=label, description=tooltip, get=get, set=set_)


PREFS = (
    ("enabled", "Enabled", "Turn Lore on or off for you"),
    (
        "prompt_lock_before_save",
        "Offer a lock when saving",
        "After saving a tracked file nobody has locked, ask to lock it",
    ),
    ("prompt_stage_after_save", "Offer to stage new files", "After saving a new file, ask to stage it"),
    ("push_after_commit", "Push after commit", "Default for the Commit dialog's Push after commit box"),
    ("keep_files_locked", "Keep files locked", "Default for the Commit dialog's Keep files locked box"),
)


def preferences_class(package: str) -> type:
    """Return the add-on preferences class for the extension `package`.

    The toggles read and write `lore_dcc_prefs.json` in Blender's config folder.
    """

    def draw(self, context):
        layout = self.layout
        for name, _label, _tooltip in PREFS:
            layout.prop(self, name)
        layout.label(text=f"Stored in {host.prefs_folder()}")

    annotations = {name: _pref_property(name, label, tip) for name, label, tip in PREFS}
    return type(
        "LORE_AddonPreferences",
        (bpy.types.AddonPreferences,),
        {"bl_idname": package, "__annotations__": annotations, "draw": draw},
    )


CLASSES = (
    *MENU_OPERATORS,
    LOCK_OUTPUTS,
    LORE_MT_main,
    LORE_OT_lock_path,
    LORE_OT_discard_path,
    LORE_OT_lock_selected,
    LORE_OT_discard_selected,
    LoreSceneSettings,
    LORE_PT_outputs,
)

MENU_HOOKS = (
    ("TOPBAR_MT_editor_menus", draw_topbar),
    ("UI_MT_button_context_menu", draw_button_menu),
    ("OUTLINER_MT_id_data", draw_outliner_menu),
    ("OUTLINER_MT_context_menu", draw_outliner_menu),
)
