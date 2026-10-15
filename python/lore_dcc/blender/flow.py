"""Actions as generators, so Lore calls leave Blender's UI thread. No bpy here.

An action is a generator that yields requests and receives their answers:

    states = yield Run(session.file_states, [path], title="Reading Lore state")
    choice = yield Ask("Lore - Lock File?", text, ["Lock", "Keep unlocked"])

A Runner performs each request and resumes the generator with `step`. The
Blender runner does `Run` on a worker thread and `Ask`/`Message` in a popup;
the inline runner answers at once, for background Blender and for tests.
An error raised by a `Run` is thrown into the generator at its `yield`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Run:
    """Call `fn(*args)` off the UI thread. `fn` must not touch bpy."""

    fn: Callable[..., Any]
    args: tuple[Any, ...] = ()
    title: str = "Working"

    def __init__(self, fn: Callable[..., Any], *args: Any, title: str = "Working") -> None:
        object.__setattr__(self, "fn", fn)
        object.__setattr__(self, "args", args)
        object.__setattr__(self, "title", title)


@dataclass(frozen=True)
class Ask:
    """Ask a question. The answer is the chosen label, or None when dismissed."""

    title: str
    text: str
    buttons: Sequence[str]
    default: str | None = None


@dataclass(frozen=True)
class Message:
    """Show a message. Resumes with None once the user closes it."""

    title: str
    text: str


@dataclass(frozen=True)
class Error:
    """Show an error. Resumes with None once the user closes it."""

    error: BaseException
    context: str = ""


@dataclass(frozen=True)
class Invoke:
    """Call a Blender operator from the main-thread timer, outside any other operator.

    `mode` is `INVOKE_DEFAULT` to open the operator's own dialog, such as
    Commit, or `EXEC_DEFAULT` to run it, such as reopening the file. Resumes
    with None once the call returns.
    """

    idname: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    mode: str = "INVOKE_DEFAULT"


Request = Run | Ask | Message | Error | Invoke
Flow = Generator[Request, Any]


class Runner(Protocol):
    """Performs requests and resumes the flow through `step`."""

    def perform(self, flow: Flow, request: Request) -> None: ...


def start(flow: Flow, runner: Runner) -> None:
    """Begin a flow."""
    step(flow, runner, None, None)


def step(flow: Flow, runner: Runner, value: Any, error: BaseException | None) -> None:
    """Resume `flow` with an answer or an error, then hand its next request to `runner`.

    A flow that raises ends there; the error is logged, since every action
    already reports its own errors.
    """
    try:
        request = flow.throw(error) if error is not None else flow.send(value)
    except StopIteration:
        return
    except Exception:
        logger.exception("Lore action failed")
        return
    runner.perform(flow, request)


def describe(error: BaseException) -> str:
    """Return the text of an error for logs and popups, with Lore's raw output when it has one."""
    message = str(error) or type(error).__name__
    details = getattr(error, "output", "") or getattr(error, "raw", "")
    lines = getattr(error, "lines", None)
    if lines:
        details = "\n".join(lines)
    return f"{message}\n\n{details.strip()}" if details and details.strip() else message


class InlineRunner:
    """Answers every request at once: runs Lore calls inline, logs messages, asks get None.

    Attributes:
        answers: Answers handed out to Ask requests in order; None when empty.
        shown: Every Ask, Message, Error and Invoke the flow made.
    """

    def __init__(self, answers: Sequence[str | None] = ()) -> None:
        self.answers = list(answers)
        self.shown: list[Request] = []

    def perform(self, flow: Flow, request: Request) -> None:
        if isinstance(request, Run):
            try:
                value = request.fn(*request.args)
            except Exception as exc:  # noqa: BLE001 - handed to the flow
                step(flow, self, None, exc)
                return
            step(flow, self, value, None)
            return
        self.shown.append(request)
        answer = None
        if isinstance(request, Ask):
            answer = self.answers.pop(0) if self.answers else None
            logger.info("%s: %s (answered %s)", request.title, request.text, answer)
        elif isinstance(request, Message):
            logger.info("%s: %s", request.title, request.text)
        elif isinstance(request, Error):
            logger.error("%s: %s", request.context or "Lore", describe(request.error))
        step(flow, self, answer, None)
