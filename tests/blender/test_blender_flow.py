"""The flow driver and the popup layout, without Blender."""

from __future__ import annotations

import pytest
from lore_dcc.blender import dialogs, flow
from lore_dcc.blender.flow import Ask, Error, InlineRunner, Message, Run
from lore_dcc.errors import LoreCommandError


def test_inline_runner_runs_calls_and_answers_asks_in_order() -> None:
    seen: list[object] = []

    def action():
        value = yield Run(lambda a, b: a + b, 2, 3, title="Adding")
        seen.append(value)
        seen.append((yield Ask("Lore - Q?", "text", ["Yes", "No"], "Yes")))
        seen.append((yield Ask("Lore - Q?", "text", ["Yes", "No"], "Yes")))
        yield Message("Lore - Done", "done")

    runner = InlineRunner(answers=["No"])
    flow.start(action(), runner)
    assert seen == [5, "No", None]
    assert [type(r).__name__ for r in runner.shown] == ["Ask", "Ask", "Message"]


def test_run_errors_are_thrown_into_the_flow() -> None:
    caught: list[str] = []

    def fail():
        raise LoreCommandError("lore x failed", ["lore", "x"], 1, "[Error] boom")

    def action():
        try:
            yield Run(fail)
        except LoreCommandError as exc:
            caught.append(exc.output)

    flow.start(action(), InlineRunner())
    assert caught == ["[Error] boom"]


def test_a_flow_that_raises_ends_without_raising_to_the_caller() -> None:
    def action():
        yield Message("Lore - A", "a")
        raise RuntimeError("bug")

    flow.start(action(), InlineRunner())


def test_describe_adds_lore_output() -> None:
    error = LoreCommandError("lore stage failed", ["lore", "stage"], 3, "[Error] file not found")
    assert flow.describe(error).endswith("[Error] file not found")
    assert flow.describe(ValueError()) == "ValueError"


@pytest.mark.parametrize(
    ("buttons", "default", "expected"),
    [
        (["Lock", "Keep unlocked"], "Lock", ("OK", ["Lock", "Keep unlocked"], False)),
        (["Discard changes", "Cancel"], "Cancel", ("Discard changes", [], True)),
        (
            ["Continue", "Sync and reopen", "Cancel"],
            "Sync and reopen",
            ("OK", ["Continue", "Sync and reopen"], False),
        ),
        (["Stage", "Not now"], "Stage", ("Stage", [], False)),
    ],
)
def test_ask_layout(buttons, default, expected) -> None:
    request = Ask("Lore - Q?", "t", buttons, default)
    title, confirm, choices, enter_cancels = dialogs.layout_of(request)
    assert title == "Lore - Q?"
    assert (confirm, choices, enter_cancels) == expected
    if choices:
        assert dialogs.first_choice(request, choices) == default


def test_messages_and_errors_have_lore_titles() -> None:
    assert dialogs.layout_of(Message("Info", "t"))[:2] == ("Lore - Info", "OK")
    assert dialogs.layout_of(Error(ValueError("x")))[0] == "Lore - Error"
    assert dialogs.body_text(Error(ValueError("x"), "Locking")) == "Locking\n\nx"


def test_text_lines_wrap_and_keep_indents_and_blank_lines() -> None:
    lines = dialogs.text_lines("short\n\n    " + "word " * 30, width=40)
    assert lines[:2] == ["short", ""]
    assert all(line.startswith("    ") and len(line) <= 40 for line in lines[2:])
    assert len(lines) > 3
