"""The one popup every question, message and error goes through.

`LORE_OT_dialog` shows the runner's current prompt in a dialog titled with
its `Lore - ` title. A question with one answer puts it on the confirm
button. A question with several answers shows them as a row of choices,
the default picked, and OK confirms the pick. Cancel, Escape or a click
outside the popup answers None, the same as closing a Houdini dialog.
"""

from __future__ import annotations

import textwrap

import bpy

from lore_dcc.blender import flow, host
from lore_dcc.blender.flow import Ask, Error, Message
from lore_dcc.ui import window_title

DISMISS = frozenset({"Cancel", "Not now", "Close"})
_WIDTH_CHARS = 90

# Blender drops enum item strings that Python stops referencing, so the items live here.
_choice_items: list[tuple[str, str, str]] = []


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
    """Return the title, confirm label, choices and whether Enter cancels.

    Dismiss words such as Cancel are the dialog's own Cancel button. One
    remaining answer goes on the confirm button. Several remaining answers
    become choices, default first picked, confirmed with OK. Enter cancels
    when the default is a dismiss word.
    """
    if isinstance(request, Error):
        return "Lore - Error", "OK", [], False
    if isinstance(request, Message):
        return window_title(request.title), "OK", [], False
    answers = [b for b in request.buttons if b not in DISMISS]
    title = window_title(request.title)
    enter_cancels = request.default in DISMISS
    if len(answers) > 1:
        return title, "OK", answers, enter_cancels
    return title, answers[0] if answers else "OK", [], enter_cancels


def first_choice(request: Ask, choices: list[str]) -> str:
    """Return the choice picked when the popup opens: the default, else the first."""
    return request.default if request.default in choices else choices[0]


def body_text(request: Ask | Message | Error) -> str:
    """Return the text shown in the popup body."""
    if isinstance(request, Error):
        text = flow.describe(request.error)
        return f"{request.context}\n\n{text}" if request.context else text
    return request.text


def _choices(self, context):
    _choice_items.clear()
    prompt = getattr(host.runner(), "current", None)
    if prompt is not None and isinstance(prompt.request, Ask):
        choices = layout_of(prompt.request)[2]
        _choice_items.extend((str(i), label, "") for i, label in enumerate(choices))
    return _choice_items or [("0", "OK", "")]


class LORE_OT_dialog(bpy.types.Operator):
    """Show a Lore question or message"""

    bl_idname = "lore.dialog"
    bl_label = "Lore"
    bl_options = {"INTERNAL"}  # noqa: RUF012 - Blender reads a set

    token: bpy.props.IntProperty(options={"SKIP_SAVE"})  # type: ignore[valid-type]
    choice: bpy.props.EnumProperty(  # type: ignore[valid-type]
        name="Answer", items=_choices, options={"SKIP_SAVE"}
    )

    def _prompt(self) -> host.Prompt | None:
        prompt = getattr(host.runner(), "current", None)
        return prompt if prompt is not None and prompt.token == self.token else None

    def invoke(self, context, event):
        prompt = self._prompt()
        if prompt is None:
            return {"CANCELLED"}
        title, confirm, choices, enter_cancels = layout_of(prompt.request)
        if choices:
            self.choice = str(choices.index(first_choice(prompt.request, choices)))
        lines = text_lines(body_text(prompt.request))
        width = min(800, max(360, 7 * max(len(line) for line in lines)))
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
        if layout_of(prompt.request)[2]:
            self.layout.row().prop(self, "choice", expand=True)

    def execute(self, context):
        prompt = self._prompt()
        if prompt is not None:
            _title, confirm, choices, _enter = layout_of(prompt.request)
            host.runner().answer(self.token, choices[int(self.choice)] if choices else confirm)
        return {"FINISHED"}

    def cancel(self, context):
        host.runner().answer(self.token, None)


CLASSES = (LORE_OT_dialog,)
