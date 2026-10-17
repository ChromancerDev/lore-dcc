"""Save and load hooks, menu actions and plugin start-up, with a fake host, session and `sd`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_sd import EmbedMethod, FakePackage, SDResourceBitmap
from lore_dcc.designer import actions, hooks, host, plugin
from lore_dcc.errors import NotAWorkingCopy
from lore_dcc.locks import LockState
from lore_dcc.prefs import Prefs
from lore_dcc.session import BehindReport, FileState, SyncResult
from lore_dcc.ui import TITLE_PREFIX


class FakeSession:
    def __init__(self, root: Path) -> None:
        self.calls: list[tuple[str, object]] = []
        self.state = {"exists": True, "tracked": True, "lock": LockState.NOT_LOCKED, "owner": None}
        self.returns: dict[str, object] = {}
        self.workspace = SimpleNamespace(
            relative=lambda p: Path(p).resolve().relative_to(root.resolve()).as_posix(), root=root
        )

    def file_states(self, paths):
        self.calls.append(("file_states", list(paths)))
        return [
            FileState(path=self.workspace.relative(p), lockable=True, **self.state) for p in paths
        ]

    def __getattr__(self, name: str):
        def record(*args):
            shown = list(args[0]) if args and isinstance(args[0], list) else list(args)
            self.calls.append((name, shown))
            return self.returns.get(name)

        return record

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def env(app, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = tmp_path / "work"
    (root / "tex").mkdir(parents=True)
    (root / ".lore").mkdir()
    sbs = root / "rock.sbs"
    sbs.write_text("x")
    bitmap = root / "tex" / "albedo.png"
    bitmap.write_text("x")
    package = FakePackage(str(sbs), resources=[SDResourceBitmap("albedo", str(bitmap))])
    app.packages = [package]
    app.current = package
    state = SimpleNamespace(
        app=app,
        session=FakeSession(root),
        prefs=Prefs(),
        asked=[],
        answers=[],
        messages=[],
        sbs=str(sbs),
        bitmap=bitmap,
        package=package,
    )

    def ask(title, text, buttons, default=None):
        assert title.startswith(TITLE_PREFIX), title
        state.asked.append((title, list(buttons)))
        state.messages.append(text)
        return state.answers.pop(0) if state.answers else None

    def message(title, text):
        assert title.startswith(TITLE_PREFIX), title
        state.asked.append((title, []))
        state.messages.append(text)

    monkeypatch.setattr(host, "load_prefs", lambda: state.prefs)
    monkeypatch.setattr(host, "save_prefs", lambda prefs: setattr(state, "prefs", prefs))
    monkeypatch.setattr(host, "session_for", lambda path: state.session)
    monkeypatch.setattr(host, "run", lambda fn, *args, title="Lore": fn(*args))
    monkeypatch.setattr(host, "later", lambda fn: fn())
    monkeypatch.setattr(host, "ask", ask)
    monkeypatch.setattr(host, "message", message)
    monkeypatch.setattr(host, "error", lambda exc, context="": state.asked.append(("error", [])))
    monkeypatch.setattr(hooks, "_callback_ids", [])
    monkeypatch.setattr(hooks, "_unknown_warned", False)
    monkeypatch.setattr(hooks, "_save_unlocked", set())
    monkeypatch.setattr(hooks, "_other_warned", set())
    return state


def test_before_save_callback_reads_the_path_from_bytes_and_offers_a_lock(env) -> None:
    hooks.install()
    env.answers.append("Lock and save")
    env.app.callback("before_save")(env.sbs.encode(), env.sbs.encode())
    assert env.asked == [("Lore - Lock File?", ["Lock and save", "Save without lock"])]
    assert env.session.names() == ["file_states", "lock"]


def test_save_without_lock_is_remembered_per_file(env) -> None:
    env.answers.append("Save without lock")
    hooks.before_save(env.sbs)
    hooks.before_save(env.sbs)
    assert [title for title, _ in env.asked] == ["Lore - Lock File?"]
    assert env.session.names() == ["file_states", "file_states"]


def test_before_save_of_a_package_never_saved_asks_nothing(env) -> None:
    hooks.before_save(str(Path(env.sbs).with_name("new.sbs")))
    assert env.asked == []
    assert env.session.calls == []


def test_locked_by_other_warns_once_and_unknown_state_warns_once(env) -> None:
    env.session.state.update(lock=LockState.LOCKED_BY_OTHER, owner="Ana")
    hooks.before_save(env.sbs)
    hooks.before_save(env.sbs)
    env.session.state.update(lock=LockState.UNKNOWN)
    hooks.before_save(env.sbs)
    hooks.before_save(env.sbs)
    assert [title for title, _ in env.asked] == [
        "Lore - File Locked by Another User", "Lore - Lock State Unknown",
    ]  # fmt: skip


def test_after_save_callback_skips_failed_saves(env) -> None:
    hooks.install()
    env.app.callback("after_save")(env.sbs.encode(), False)
    assert env.session.calls == []


def test_after_save_marks_then_offers_to_stage_with_the_references(env) -> None:
    hooks.install()
    env.session.state.update(tracked=False)
    env.answers.append("Stage")
    env.app.callback("after_save")(env.sbs.encode(), True)
    assert env.asked == [("Lore - Stage New File?", ["Stage", "Not now"])]
    assert env.session.names() == [
        "mark_saved", "file_states", "is_staged", "set_references", "stage",
    ]  # fmt: skip
    assert env.session.calls[3] == ("set_references", [env.sbs, (env.bitmap.resolve(),)])


def test_disabled_and_outside_a_working_copy_do_nothing(env, monkeypatch) -> None:
    env.prefs = Prefs(enabled=False)
    hooks.before_save(env.sbs)
    hooks.after_save(env.sbs)
    hooks.after_load(env.sbs)
    env.prefs = Prefs()

    def refuse(path):
        raise NotAWorkingCopy("no")

    monkeypatch.setattr(host, "session_for", refuse)
    hooks.before_save(env.sbs)
    hooks.after_save(env.sbs)
    assert env.session.calls == []
    assert env.asked == []


def test_actions_need_a_graph_in_focus_and_a_saved_package(env) -> None:
    env.app.current = None
    actions.lock()
    env.app.current = FakePackage("")
    actions.lock()
    assert env.asked == [("Lore - No Package", []), ("Lore - Save File First", [])]
    assert env.session.calls == []


def test_lock_locks_the_current_package(env) -> None:
    actions.lock()
    assert env.session.calls == [("lock", [env.sbs])]
    assert env.messages == ["Locked rock.sbs."]


def test_discard_confirms_then_reloads_the_package(env) -> None:
    env.answers.append("Discard changes")
    actions.discard()
    assert env.session.names() == ["file_states", "discard"]
    assert env.app.events == [("unload", env.sbs), ("load", env.sbs)]


def test_explorer_lock_uses_the_selected_linked_resource(env) -> None:
    env.app.ui.selection = [env.package.resources[0]]
    actions.lock_selection(7)
    assert env.session.calls == [("lock", [str(env.bitmap)])]


def test_explorer_lock_with_nothing_selected_locks_the_current_package(env) -> None:
    actions.lock_selection(7)
    assert env.session.calls == [("lock", [env.sbs])]


def test_sync_reloads_open_packages_and_asks_to_reopen_for_bitmaps(env) -> None:
    env.session.returns["behind"] = BehindReport(False, ("tex/albedo.png",))
    env.session.returns["sync_references"] = SyncResult(("tex/albedo.png",), ())
    env.answers.append("Sync")
    actions.sync_dependencies()
    assert [title for title, _ in env.asked] == [
        "Lore - Sync Dependencies?", "Lore - Sync Dependencies",
    ]  # fmt: skip
    assert "tex/albedo.png" in env.messages[-1]
    assert "reopen" in env.messages[-1]
    assert env.app.events == []


def test_after_load_offer_syncs_the_loaded_package(env, monkeypatch) -> None:
    from lore_dcc.ui import tasks

    env.session.returns["behind"] = BehindReport(False, ("tex/albedo.png",))
    env.session.returns["sync_references"] = SyncResult(("tex/albedo.png",), ())
    monkeypatch.setattr(
        tasks, "run_in_background", lambda fn, *a, on_done: on_done(fn(*a), None)
    )
    env.app.current = None
    env.answers.append("Sync Dependencies")
    hooks.install()
    env.app.callback("after_load")(env.sbs.encode(), True, True)
    assert [title for title, _ in env.asked] == [
        "Lore - Dependencies Behind Server", "Lore - Sync Dependencies",
    ]  # fmt: skip
    assert "sync_references" in env.session.names()


def test_plugin_builds_the_menu_and_removes_everything_on_unload(env) -> None:
    plugin.initialize()
    menu = env.app.ui.menus[plugin.MENU_OBJECT]
    labels = [entry.label if entry else None for entry in menu.entries]
    assert labels == [
        "Info", "Lock", "Discard changes", "Sync Dependencies", "Commit", None,
        "Enable/Disable", None, "File a Bug",
    ]  # fmt: skip
    kinds = sorted(kind for kind, _ in env.app.callbacks.values())
    assert kinds == ["after_load", "after_save", "before_save", "explorer"]
    plugin.uninitialize()
    assert env.app.ui.menus == {}
    assert env.app.callbacks == {}


def test_menu_action_runs_its_action(env) -> None:
    plugin.initialize()
    menu = env.app.ui.menus[plugin.MENU_OBJECT]
    enable = next(e for e in menu.entries if e and e.label == "Enable/Disable")
    enable.slots[0](False)
    assert env.prefs.enabled is False
    plugin.uninitialize()


def test_hooks_skip_files_outside_every_working_copy_without_asking_lore(
    env, monkeypatch, tmp_path
) -> None:
    outside = tmp_path / "library" / "blend.sbs"
    outside.parent.mkdir()
    outside.write_text("x")
    asked_lore: list[str] = []
    monkeypatch.setattr(host, "session_for", asked_lore.append)
    hooks.before_save(str(outside))
    hooks.after_save(str(outside))
    hooks.after_load(str(outside))
    assert asked_lore == []
    assert env.asked == []


def test_explorer_lock_on_a_graph_locks_its_package(env) -> None:
    graph = SDResourceBitmap("graph", "", EmbedMethod.Undefined)
    graph.getPackage = lambda: env.package
    env.app.ui.selection = [graph]
    actions.lock_selection(7)
    assert env.session.calls == [("lock", [env.sbs])]


def test_api_lock_and_stage_group_paths_by_working_copy_and_skip_the_rest(env, tmp_path) -> None:
    from lore_dcc.designer import api
    from lore_dcc.session import WriteResult

    env.session.returns["prepare_write"] = ("out/rock.sbsar",)
    env.session.returns["finish_write"] = WriteResult(("out/rock.sbsar",), (), ())
    inside = str(Path(env.sbs).parent / "out" / "rock.sbsar")
    outside = str(tmp_path / "elsewhere" / "rock.sbsar")
    assert api.lock([inside, outside]) == ("out/rock.sbsar",)
    assert api.stage(inside) == WriteResult(("out/rock.sbsar",), (), ())
    assert env.session.calls == [
        ("prepare_write", [Path(inside)]),
        ("finish_write", [Path(inside)]),
    ]
    assert api.lock([outside]) == ()
