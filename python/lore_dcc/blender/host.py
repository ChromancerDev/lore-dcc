"""Glue between Blender and the plugin core: prefs, sessions, the flow runner, popups.

Lore calls run on one worker thread in call order. A timer on Blender's main
thread delivers their results and opens popups one at a time, so a flow never
blocks the UI. Background Blender (`blender -b`) runs everything inline and
logs instead of asking.
"""

from __future__ import annotations

import collections
import itertools
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bpy
from lore_dcc import prefs as prefs_module
from lore_dcc import session as session_module
from lore_dcc.blender import flow
from lore_dcc.blender.flow import Ask, Error, Flow, InlineRunner, Invoke, Message, Request, Run
from lore_dcc.prefs import Prefs
from lore_dcc.session import Session

logger = logging.getLogger(__name__)

_IDLE_INTERVAL = 0.25
_BUSY_INTERVAL = 0.05


def has_ui() -> bool:
    """Return True in a graphical Blender session."""
    return not bpy.app.background


def prefs_folder() -> Path:
    """Return the folder that holds the per-user prefs file: Blender's user config folder."""
    return Path(bpy.utils.user_resource("CONFIG"))


def load_prefs() -> Prefs:
    """Read the user's prefs."""
    return prefs_module.load_prefs(prefs_folder())


def save_prefs(prefs: Prefs) -> None:
    """Write the user's prefs."""
    prefs_module.save_prefs(prefs_folder(), prefs)


def session_for(path: Path | str) -> Session:
    """Return the Blender Session for the working copy holding `path`. Safe off the main thread.

    Raises:
        NotAWorkingCopy: `path` is not in a Lore working copy.
        LoreError: Lore is missing or too old.
    """
    return session_module.session_for(path, session_module.BLENDER)


@dataclass
class Prompt:
    """A popup waiting for, or showing to, the user.

    Attributes:
        request: The Ask, Message or Error to show.
        answer: Called once with the chosen label, or None when dismissed.
        token: Tells this popup apart from the next, so a late Cancel cannot answer it.
    """

    request: Ask | Message | Error
    answer: Callable[[str | None], None]
    token: int = 0


class BlenderRunner:
    """Runs flows in the UI: Lore calls on a worker thread, popups from a main-thread timer."""

    def __init__(self) -> None:
        self._jobs: queue.Queue[tuple[Flow, Run]] = queue.Queue()
        self._done: queue.Queue[tuple[Flow, Any, BaseException | None]] = queue.Queue()
        self._prompts: collections.deque[Prompt] = collections.deque()
        self._ui: collections.deque[tuple[Flow, Invoke]] = collections.deque()
        self.current: Prompt | None = None
        self.busy: str = ""
        self._pending = 0
        self._tokens = itertools.count(1)
        self._worker = threading.Thread(target=self._work, name="lore-dcc-worker", daemon=True)
        self._worker.start()

    def perform(self, flow_: Flow, request: Request) -> None:
        if isinstance(request, Run):
            self._pending += 1
            self.busy = request.title
            self._jobs.put((flow_, request))
        elif isinstance(request, Invoke):
            self._ui.append((flow_, request))
        else:
            self.prompt(request, lambda answer: flow.step(flow_, self, answer, None))

    def prompt(self, request: Ask | Message | Error, answer: Callable[[str | None], None]) -> None:
        """Queue a popup. Safe from any thread; it opens on the next tick."""
        self._prompts.append(Prompt(request, answer, next(self._tokens)))

    def answer(self, token: int, label: str | None) -> None:
        """Answer the popup `token` with `label`. Ignored unless that popup is the current one."""
        current = self.current
        if current is None or current.token != token:
            return
        self.current = None
        current.answer(label)

    def _work(self) -> None:
        while True:
            flow_, request = self._jobs.get()
            try:
                self._done.put((flow_, request.fn(*request.args), None))
            except BaseException as exc:  # noqa: BLE001 - handed back to the flow
                self._done.put((flow_, None, exc))

    def tick(self) -> float:
        """Deliver finished Lore calls and open the next popup. Runs on the main thread."""
        while True:
            try:
                flow_, value, error = self._done.get_nowait()
            except queue.Empty:
                break
            self._pending -= 1
            if not self._pending:
                self.busy = ""
            flow.step(flow_, self, value, error)
        while self._ui:
            flow_, request = self._ui.popleft()
            try:
                _invoke(request.idname, request.kwargs, request.mode)
            except Exception as exc:  # noqa: BLE001 - handed to the flow
                flow.step(flow_, self, None, exc)
            else:
                flow.step(flow_, self, None, None)
        if self.current is None and self._prompts:
            self.current = self._prompts.popleft()
            try:
                _invoke("lore.dialog", {"token": self.current.token})
            except Exception:
                logger.exception("Lore could not open a popup")
                self.answer(self.current.token, None)
        _show_busy(self.busy)
        return _BUSY_INTERVAL if self._pending or self._prompts or self._ui else _IDLE_INTERVAL


_runner: BlenderRunner | InlineRunner | None = None
_busy_shown = ""


def runner() -> BlenderRunner | InlineRunner:
    """Return the session's runner, creating it on first use."""
    global _runner
    if _runner is None:
        _runner = BlenderRunner() if has_ui() else InlineRunner()
    return _runner


def start(action: Flow) -> None:
    """Begin a flow on the session's runner."""
    flow.start(action, runner())


def tick() -> float | None:
    """Timer callback: pump the runner. Returns the next interval."""
    try:
        current = runner()
        return current.tick() if isinstance(current, BlenderRunner) else None
    except Exception:
        logger.exception("Lore timer failed")
        return _IDLE_INTERVAL


def install_timer() -> None:
    """Start the pump timer once per Blender session."""
    if has_ui() and not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=_IDLE_INTERVAL, persistent=True)


def remove_timer() -> None:
    """Stop the pump timer."""
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)


def notify(request: Message | Error) -> None:
    """Show a message or error from code that is not a flow, from any thread.

    Without a UI it is logged.
    """
    current = runner()
    if isinstance(current, BlenderRunner):
        current.prompt(request, lambda _answer: None)
    else:
        current.shown.append(request)
        if isinstance(request, Error):
            logger.error("%s: %s", request.context or "Lore", flow.describe(request.error))
        else:
            logger.info("%s: %s", request.title, request.text)


def window_context() -> dict[str, Any]:
    """Return a context override with a window, its largest area and that area's main region."""
    wm = bpy.context.window_manager
    window = bpy.context.window or (wm.windows[0] if wm.windows else None)
    if window is None:
        return {}
    areas = sorted(window.screen.areas, key=lambda a: a.width * a.height, reverse=True)
    override: dict[str, Any] = {"window": window, "screen": window.screen}
    if areas:
        regions = [r for r in areas[0].regions if r.type == "WINDOW"]
        override["area"] = areas[0]
        if regions:
            override["region"] = regions[0]
    return override


def _invoke(idname: str, kwargs: dict[str, Any], mode: str = "INVOKE_DEFAULT") -> None:
    category, name = idname.split(".")
    operator = getattr(getattr(bpy.ops, category), name)
    with bpy.context.temp_override(**window_context()):
        operator(mode, **kwargs)


def _show_busy(title: str) -> None:
    global _busy_shown
    if title == _busy_shown:
        return
    _busy_shown = title
    try:
        with bpy.context.temp_override(**window_context()):
            bpy.context.workspace.status_text_set(f"Lore: {title}..." if title else None)
    except Exception:
        logger.debug("Lore could not set the status bar", exc_info=True)
