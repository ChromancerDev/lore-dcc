"""The one popup every question, message and error goes through.

`LORE_OT_dialog` shows the runner's current prompt in a dialog titled with
its `Lore - ` title. A question's main answer is the dialog's confirm button;
other answers are buttons in the body. Cancel, or closing the popup,
answers None, the same as Houdini's close button.
"""

from __future__ import annotations

import textwrap

import bpy
from lore_dcc.blender import flow, host
from lore_dcc.blender.flow import Ask, Error, Message
from lore_dcc.ui import window_title

DISMISS = frozenset({"Cancel", "Not now", "Close"})
_WIDTH_CHARS = 90


def text_lines(text: str, width: int = _WIDTH_CHARS) -> list[str]:
    """Return `text` wrapped for Blender labels, which never wrap themselves. Blank lines stay."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        indent = paragraph[: len(paragraph) - len(paragraph.lstrip())]
        wrapped = textwrap.wrap(
            paragraph.strip(), width - len(indent), break_long_words=True, break_on_hyphens=False
        )
        lines += [indent + part for part in wrapped] or [""]
    return lines


def layout_of(request: Ask | Message | Error) -> tuple[str, str, list[str], bool]:
    """Return the title, confirm label, body buttons and whether Enter cancels.

    The confirm button is the default answer unless that is a dismiss word
    such as Cancel, in which case it is the first other answer and Enter cancels.
    """
    if isinstance(request, Error):
        return "Lore - Error", "OK", [], False
    if isinstance(request, Message):
        return window_title(request.title), "OK", [], False
    answers = [b for b in request.buttons if b not in DISMISS]
    default = request.default if request.default in answers else None
    confirm = default or (answers[0] if answers else "OK")
    others = [b for b in answers if b != confirm]
    return window_title(request.title), confirm, others, request.default in DISMISS


def body_text(request: Ask | Message | Error) -> str:
    """Return the text shown in the popup body."""
    if isinstance(request, Error):
        text = flow.describe(request.error)
        return f"{request.context}\n\n{text}" if request.context else text
    return request.text


class LORE_OT_dialog(bpy.types.Operator):
    """Show a Lore question or message"""

    bl_idname = "lore.dialog"
    bl_label = "Lore"
    bl_options = {"INTERNAL"}  # noqa: RUF012 - Blender reads a set

    token: bpy.props.IntProperty(options={"SKIP_SAVE"})  # type: ignore[valid-type]

    def _prompt(self) -> host.Prompt | None:
        prompt = getattr(host.runner(), "current", None)
        return prompt if prompt is not None and prompt.token == self.token else None

    def invoke(self, context, event):
        prompt = self._prompt()
        if prompt is None:
            return {"CANCELLED"}
        title, confirm, _others, enter_cancels = layout_of(prompt.request)
        width = min(800, max(360, 7 * max(len(line) for line in text_lines(body_text(prompt.request)))))
        return context.window_manager.invoke_props_dialog(
            self, width=width, title=title, confirm_text=confirm, cancel_default=enter_cancels
        )

    def draw(self, context):
        prompt = self._prompt()
        if prompt is None:
            return
        column = self.layout.column(align=True)
        for line in text_lines(body_text(prompt.request)):
            column.label(text=line)
        _title, _confirm, others, _enter = layout_of(prompt.request)
        if others:
            row = self.layout.row()
            for label in others:
                button = row.operator(LORE_OT_dialog_answer.bl_idname, text=label)
                button.token = self.token
                button.choice = label

    def execute(self, context):
        prompt = self._prompt()
        if prompt is not None:
            _title, confirm, _others, _enter = layout_of(prompt.request)
            host.runner().answer(self.token, confirm)
        return {"FINISHED"}

    def cancel(self, context):
        host.runner().answer(self.token, None)


class LORE_OT_dialog_answer(bpy.types.Operator):
    """Answer the open Lore question with this choice"""

    bl_idname = "lore.dialog_answer"
    bl_label = "Answer"
    bl_options = {"INTERNAL"}  # noqa: RUF012 - Blender reads a set

    token: bpy.props.IntProperty(options={"SKIP_SAVE"})  # type: ignore[valid-type]
    choice: bpy.props.StringProperty(options={"SKIP_SAVE"})  # type: ignore[valid-type]

    def execute(self, context):
        host.runner().answer(self.token, self.choice)
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        return {"FINISHED"}


CLASSES = (LORE_OT_dialog, LORE_OT_dialog_answer)
