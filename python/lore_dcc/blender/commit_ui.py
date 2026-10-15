"""The Commit dialog as a Blender popup.

`prepare` fills a list on the window manager from `commit_model` rows, with
one header row per group whose checkbox toggles the whole group. The dialog's
Commit button starts `actions.run_commit`; a missing summary or an empty
selection shows why and opens the dialog again with everything kept.
"""

from __future__ import annotations

from collections.abc import Sequence

import bpy

from lore_dcc import commit_model
from lore_dcc.backend.parsers import Status
from lore_dcc.blender import actions, host
from lore_dcc.blender.flow import Invoke, Message
from lore_dcc.commit_model import CommitRow
from lore_dcc.prefs import Prefs
from lore_dcc.session import Session

_session: Session | None = None
_updating = False


def _settings(context) -> LoreCommitSettings:
    return context.window_manager.lore_commit


def _row_checked(row: LoreCommitRow, context) -> None:
    global _updating
    if _updating:
        return
    _updating = True
    try:
        rows = _settings(context).rows
        members = [r for r in rows if r.group == row.group and not r.is_header and r.enabled]
        if row.is_header:
            for member in members:
                member.checked = row.checked
        else:
            header = next(r for r in rows if r.is_header and r.group == row.group)
            header.checked = bool(members) and all(m.checked for m in members)
    finally:
        _updating = False


class LoreCommitRow(bpy.types.PropertyGroup):
    """One file, or one group header, in the Commit dialog."""

    path: bpy.props.StringProperty()  # type: ignore[valid-type]
    status: bpy.props.StringProperty()  # type: ignore[valid-type]
    group: bpy.props.StringProperty()  # type: ignore[valid-type]
    lock_text: bpy.props.StringProperty()  # type: ignore[valid-type]
    is_header: bpy.props.BoolProperty()  # type: ignore[valid-type]
    enabled: bpy.props.BoolProperty(default=True)  # type: ignore[valid-type]
    checked: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Include", description="Include this file in the commit", update=_row_checked
    )


class LoreCommitSettings(bpy.types.PropertyGroup):
    """The Commit dialog's state, kept while the dialog reopens."""

    rows: bpy.props.CollectionProperty(type=LoreCommitRow)  # type: ignore[valid-type]
    active: bpy.props.IntProperty()  # type: ignore[valid-type]
    branch_text: bpy.props.StringProperty()  # type: ignore[valid-type]
    summary: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Summary", description="One line on what changed. Required"
    )
    description: bpy.props.StringProperty(  # type: ignore[valid-type]
        name="Description", description="More detail. Optional"
    )
    push: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Push after commit",
        description=(
            "Commit records a revision locally. Push sends it to the server. "
            "Together they are what Perforce calls Submit"
        ),
        default=True,
    )
    keep_locked: bpy.props.BoolProperty(  # type: ignore[valid-type]
        name="Keep files locked",
        description="Keep your locks after the push. Perforce: keep files checked out",
    )


def checked_paths(rows: Sequence[LoreCommitRow]) -> list[str]:
    """Return the repo-relative paths of every checked, enabled file row."""
    return [r.path for r in rows if r.checked and r.enabled and not r.is_header]


def prepare(session: Session, status: Status, rows: Sequence[CommitRow], prefs: Prefs) -> None:
    """Fill the dialog's list and reset its fields for a new commit."""
    global _session
    _session = session
    settings = bpy.context.window_manager.lore_commit
    global _updating
    _updating = True
    try:
        settings.rows.clear()
        for group in commit_model.GROUPS:
            members = [r for r in rows if r.group == group]
            if not members:
                continue
            header = settings.rows.add()
            header.is_header, header.group = True, group
            header.path = f"{group} ({len(members)})"
            enabled = [r for r in members if r.enabled]
            header.enabled = bool(enabled)
            header.checked = bool(enabled) and all(r.checked for r in enabled)
            for row in members:
                item = settings.rows.add()
                item.path, item.status, item.group = row.path, row.status_word, row.group
                item.lock_text, item.enabled, item.checked = row.lock_text, row.enabled, row.checked
    finally:
        _updating = False
    remote = commit_model.remote_state_text(status.remote_state)
    settings.branch_text = f"Branch {status.branch}  |  {remote}"
    settings.summary = ""
    settings.description = ""
    settings.push = prefs.push_after_commit
    settings.keep_locked = prefs.keep_files_locked
    settings.active = 0


class LORE_UL_commit_rows(bpy.types.UIList):
    """Changed files, grouped as This session, Outputs, Dependencies, Other changes, Cannot commit"""

    bl_idname = "LORE_UL_commit_rows"

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        if item.is_header:
            row = layout.row()
            row.enabled = item.enabled
            row.prop(item, "checked", text=item.path, emboss=True)
            return
        split = layout.split(factor=0.05)
        box = split.row()
        box.enabled = item.enabled
        box.prop(item, "checked", text="")
        split = split.split(factor=0.12)
        split.label(text=item.status)
        split = split.split(factor=0.75)
        split.label(text=item.path)
        split.label(text=item.lock_text)


class LORE_OT_commit_dialog(bpy.types.Operator):
    """Commit the checked files to Lore"""

    bl_idname = "lore.commit_dialog"
    bl_label = "Commit"
    bl_options = {"INTERNAL"}  # noqa: RUF012 - Blender reads a set

    def invoke(self, context, event):
        if _session is None:
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(
            self, width=820, title="Lore - Commit", confirm_text="Commit"
        )

    def draw(self, context):
        settings = _settings(context)
        layout = self.layout
        layout.label(text=settings.branch_text)
        layout.template_list(
            LORE_UL_commit_rows.bl_idname, "", settings, "rows", settings, "active", rows=14
        )
        layout.prop(settings, "summary")
        layout.prop(settings, "description")
        row = layout.row()
        row.prop(settings, "push")
        row.prop(settings, "keep_locked")
        count = len(checked_paths(settings.rows))
        hint = commit_model.commit_hint(settings.summary, count)
        footer = layout.row()
        footer.label(text=commit_model.files_label(count))
        if hint:
            footer.label(text=hint, icon="ERROR")

    def execute(self, context):
        settings = _settings(context)
        include = checked_paths(settings.rows)
        hint = commit_model.commit_hint(settings.summary, len(include))
        if hint or _session is None:
            host.start(_reopen(hint))
            return {"FINISHED"}
        host.start(
            actions.run_commit(
                _session,
                include,
                settings.summary.strip(),
                settings.description.strip(),
                settings.push,
                settings.keep_locked,
            )
        )
        return {"FINISHED"}


def _reopen(hint: str):
    yield Message("Lore - Commit", hint)
    yield Invoke(LORE_OT_commit_dialog.bl_idname)


CLASSES = (LoreCommitRow, LoreCommitSettings, LORE_UL_commit_rows, LORE_OT_commit_dialog)


def register() -> None:
    """Attach the dialog's state to the window manager. Call after the classes are registered."""
    bpy.types.WindowManager.lore_commit = bpy.props.PointerProperty(type=LoreCommitSettings)


def unregister() -> None:
    """Remove the dialog's state."""
    if hasattr(bpy.types.WindowManager, "lore_commit"):
        del bpy.types.WindowManager.lore_commit
