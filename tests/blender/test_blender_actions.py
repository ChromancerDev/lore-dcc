"""Decision logic of the Blender actions and save hooks, with a fake session and no Blender."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import bpy
import pytest
from lore_dcc.backend.parsers import Status
from lore_dcc.blender import actions, flow, hooks, host, outputs, references
from lore_dcc.blender.flow import Error, InlineRunner
from lore_dcc.commit_model import CommitRow
from lore_dcc.errors import NotAWorkingCopy
from lore_dcc.locks import LockState
from lore_dcc.references import SceneReferences
from lore_dcc.session import BehindReport, CommitResult, FileState, SyncResult, WriteResult
from lore_dcc.ui import TITLE_PREFIX


class FakeSession:
    def __init__(self, root: Path) -> None:
        self.calls: list[tuple[str, object]] = []
        self.state = {"exists": True, "tracked": True, "lock": LockState.NOT_LOCKED, "owner": None}
        self.returns: dict[str, object] = {}
        self.workspace = SimpleNamespace(relative=lambda p: Path(p).name, root=root)
        self.lore = SimpleNamespace(check_dirty=lambda paths: Status("main", 1, 1, None, (), (), ()))

    def file_states(self, paths):
        self.calls.append(("file_states", list(paths)))
        return [
            FileState(path=Path(p).name, lockable=True, **self.state) for p in paths
        ]

    def __getattr__(self, name: str):
        def record(*args):
            shown = list(args[0]) if args and isinstance(args[0], list) else list(args)
            self.calls.append((name, shown))
            value = self.returns.get(name)
            if isinstance(value, BaseException):
                raise value
            return value

        return record

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    blend = tmp_path / "shot.blend"
    blend.write_text("x")
    session = FakeSession(tmp_path)
    state = SimpleNamespace(
        session=session,
        blend=str(blend),
        runner=InlineRunner(),
        scene=SceneReferences((tmp_path / "tex" / "a.png",), ()),
        reloaded=[],
        stale=[],
    )
    monkeypatch.setattr(bpy.data, "filepath", str(blend))
    monkeypatch.setattr(host, "session_for", lambda path: session)
    monkeypatch.setattr(references, "scan", lambda root: state.scene)

    def reload(paths):
        state.reloaded.append([str(p) for p in paths])
        return state.stale

    monkeypatch.setattr(references, "reload", reload)
    monkeypatch.setattr(bpy.context, "scene", SimpleNamespace(name="Scene"), raising=False)

    def run(action, answers=()):
        state.runner = InlineRunner(answers)
        flow.start(action, state.runner)
        for request in state.runner.shown:
            title = getattr(request, "title", "Lore - Error")
            assert title.startswith(TITLE_PREFIX), title
        return state.runner.shown

    state.run = run
    return state


def kinds(shown) -> list[tuple[str, str]]:
    return [(type(r).__name__, getattr(r, "title", getattr(r, "idname", ""))) for r in shown]


def test_actions_without_a_saved_file_ask_to_save(env, monkeypatch) -> None:
    monkeypatch.setattr(bpy.data, "filepath", "")
    for action in (actions.info, actions.lock, actions.discard, actions.commit):
        assert kinds(env.run(action())) == [("Message", "Lore - Save File First")]
    assert env.session.calls == []


def test_disabled_prefs_stop_every_action(env) -> None:
    host.save_prefs(host.load_prefs().__class__(enabled=False))
    assert kinds(env.run(actions.lock())) == [("Message", "Lore - Disabled")]
    assert env.session.calls == []


def test_outside_a_working_copy(env, monkeypatch) -> None:
    def outside(path):
        raise NotAWorkingCopy(str(path))

    monkeypatch.setattr(host, "session_for", outside)
    assert kinds(env.run(actions.lock())) == [("Message", "Lore - Not a Working Copy")]


def test_toggle_enabled_flips_the_pref(env) -> None:
    env.run(actions.toggle_enabled())
    assert host.load_prefs().enabled is False
    assert kinds(env.run(actions.toggle_enabled())) == [("Message", "Lore - Enabled")]
    assert host.load_prefs().enabled is True


def test_file_a_bug_names_blender(env, monkeypatch) -> None:
    from lore_dcc import bugs

    opened: list[tuple[str, str]] = []
    monkeypatch.setattr(bugs, "open_report", lambda dcc, version: opened.append((dcc, version)))
    shown = env.run(actions.file_a_bug())
    assert opened == [("Blender", bpy.app.version_string)]
    assert kinds(shown) == [("Message", "Lore - File a Bug")]


def test_lock_locks_the_open_file(env) -> None:
    shown = env.run(actions.lock())
    assert ("lock", [env.blend]) in env.session.calls
    assert shown[-1].text == "Locked shot.blend."


def test_discard_asks_then_reverts(env) -> None:
    shown = env.run(actions.discard(), answers=["Discard changes"])
    assert ("discard", [env.blend]) in env.session.calls
    assert kinds(shown) == [("Ask", "Lore - Discard Changes?"), ("Invoke", "wm.revert_mainfile")]
    assert shown[-1].mode == "EXEC_DEFAULT"


def test_discard_cancelled_does_nothing(env) -> None:
    env.run(actions.discard(), answers=[None])
    assert "discard" not in env.session.names()


def test_discard_refuses_a_new_file(env) -> None:
    env.session.state["tracked"] = False
    shown = env.run(actions.discard())
    assert kinds(shown) == [("Message", "Lore - Discard Changes")]
    assert "is new" in shown[0].text
    assert "discard" not in env.session.names()


def test_discard_files_reloads_a_library_and_lists_what_cannot_reload(env, tmp_path) -> None:
    image = tmp_path / "a.png"
    image.write_text("x")
    env.stale = ["Sound hit"]
    shown = env.run(actions.discard_files([str(image)]), answers=["Discard changes"])
    assert env.reloaded == [[str(image)]]
    assert kinds(shown)[-1] == ("Message", "Lore - Discard Changes")
    assert "Sound hit" in shown[-1].text


def test_lock_files_refuses_missing_paths(env, tmp_path) -> None:
    shown = env.run(actions.lock_files([str(tmp_path / "missing.png"), ""]))
    assert kinds(shown) == [("Message", "Lore - File")]
    assert "does not exist" in shown[0].text and "empty" in shown[0].text
    assert env.session.calls == []


def test_collect_outputs_reports_the_write(env, monkeypatch, tmp_path) -> None:
    frame = tmp_path / "render" / "f_0001.png"
    monkeypatch.setattr(outputs, "scene_outputs", lambda scene, root, notes: [frame])
    env.session.returns["finish_write"] = WriteResult(("render/f_0001.png",), (), ())
    shown = env.run(actions.collect_outputs())
    assert ("finish_write", [frame]) in env.session.calls
    assert shown[-1].text.startswith("Staged (new): 1")


def test_collect_outputs_with_nothing_to_collect(env, monkeypatch) -> None:
    monkeypatch.setattr(outputs, "scene_outputs", lambda scene, root, notes: [])
    shown = env.run(actions.collect_outputs())
    assert "writes no files" in shown[-1].text


def test_sync_dependencies_up_to_date(env) -> None:
    env.session.returns["behind"] = BehindReport(False, ())
    shown = env.run(actions.sync_dependencies())
    assert ("set_references", [env.blend, env.scene.inside]) in env.session.calls
    assert "up to date" in shown[-1].text


def test_sync_dependencies_syncs_and_reloads_behind_references(env, tmp_path) -> None:
    env.session.returns["behind"] = BehindReport(False, ("tex/a.png",))
    env.session.returns["sync_references"] = SyncResult(("tex/a.png",), ())
    shown = env.run(actions.sync_dependencies(), answers=["Sync"])
    assert ("sync_references", ["tex/a.png"]) in env.session.calls
    assert env.reloaded == [[str(tmp_path / "tex/a.png")]]
    assert kinds(shown) == [("Ask", "Lore - Sync Dependencies?"), ("Message", "Lore - Sync Dependencies")]


def test_sync_and_reopen_syncs_the_blend_and_reverts(env) -> None:
    env.session.returns["behind"] = BehindReport(True, ())
    env.session.returns["sync_references"] = SyncResult(("shot.blend",), ())
    shown = env.run(actions.sync_dependencies(), answers=["Sync and reopen"])
    assert ("sync_references", ["shot.blend"]) in env.session.calls
    assert kinds(shown) == [
        ("Ask", "Lore - Scene Behind Server"),
        ("Invoke", "wm.revert_mainfile"),
        ("Message", "Lore - Sync Dependencies"),
    ]
    assert env.reloaded == []


def test_sync_and_reopen_with_unsaved_changes_asks_first(env, monkeypatch) -> None:
    monkeypatch.setattr(bpy.data, "is_dirty", True)
    env.session.returns["behind"] = BehindReport(True, ())
    shown = env.run(actions.sync_dependencies(), answers=["Sync and reopen", None])
    assert kinds(shown)[-1] == ("Ask", "Lore - Discard Unsaved Changes?")
    assert "sync_references" not in env.session.names()


def _row(path: str, group: str = "This session") -> CommitRow:
    return CommitRow(path, "A", group, True, True, "", None, "")


def test_commit_drops_backups_and_opens_the_dialog(env, monkeypatch) -> None:
    from lore_dcc import commit_model
    from lore_dcc.blender import commit_ui

    status = Status("main", 1, 1, None, (), (), ())
    rows = [_row("shot.blend"), _row("shot.blend1", "Other changes"), _row("x.blend12")]
    monkeypatch.setattr(commit_model, "load_rows", lambda session: (status, rows))
    prepared: list[list[str]] = []
    monkeypatch.setattr(commit_ui, "prepare", lambda s, st, r, p: prepared.append([x.path for x in r]))
    shown = env.run(actions.commit())
    assert prepared == [["shot.blend"]]
    assert kinds(shown) == [("Invoke", "lore.commit_dialog")]


def test_commit_warns_about_outside_references_and_reports_no_changes(env, monkeypatch) -> None:
    from lore_dcc import commit_model

    env.scene = SceneReferences((), (("Image a", "D:/ext/a.png"),))
    monkeypatch.setattr(commit_model, "load_rows", lambda s: (Status("main", 1, 1, None, (), (), ()), []))
    shown = env.run(actions.commit())
    assert kinds(shown) == [
        ("Message", "Lore - References Outside the Repo"),
        ("Message", "Lore - Commit"),
    ]


def test_run_commit_retries_a_failed_push_then_reports(env) -> None:
    failed = CommitResult(False, True, "[Error] Branch has diverged", "", revision=21)
    pushed = CommitResult(True, False, None, "", branch="main", server_revision=21)
    env.session.returns["commit"] = failed
    env.session.returns["retry_push"] = pushed
    shown = env.run(
        actions.run_commit(env.session, ["shot.blend"], "Sum", "", True, False),
        answers=["Retry push"],
    )
    assert kinds(shown) == [("Ask", "Lore - Push Failed"), ("Message", "Lore - Commit Complete")]
    assert shown[-1].text.startswith("Committed revision 21 and pushed it to main.")


def test_run_commit_without_push_reports_at_once(env) -> None:
    env.session.returns["commit"] = CommitResult(False, False, None, "", revision=5)
    shown = env.run(actions.run_commit(env.session, ["a"], "Sum", "", False, True))
    assert kinds(shown) == [("Message", "Lore - Commit Complete")]
    assert env.session.calls == [("commit", ["a"])]


def test_lore_errors_become_an_error_popup(env) -> None:
    from lore_dcc.errors import LockNotAcquired

    env.session.returns["lock"] = LockNotAcquired(["shot.blend"], "shot.blend locked by sam")
    shown = env.run(actions.lock())
    assert isinstance(shown[-1], Error)


# Save and load hooks


def test_after_save_offers_a_lock_on_an_unlocked_tracked_file(env) -> None:
    shown = env.run(hooks.after_save(env.blend), answers=["Lock"])
    assert kinds(shown) == [("Ask", "Lore - Lock File?")]
    assert env.session.names()[:2] == ["mark_saved", "file_states"]
    assert ("lock", [env.blend]) in env.session.calls


def test_keep_unlocked_is_remembered_for_the_session(env) -> None:
    env.run(hooks.after_save(env.blend), answers=["Keep unlocked"])
    assert kinds(env.run(hooks.after_save(env.blend))) == []


def test_lock_prompt_pref_off(env) -> None:
    host.save_prefs(host.load_prefs().__class__(prompt_lock_before_save=False))
    assert kinds(env.run(hooks.after_save(env.blend))) == []


def test_locked_by_another_user_warns_once(env, monkeypatch) -> None:
    monkeypatch.setattr(host, "has_ui", lambda: True)
    env.session.state.update(lock=LockState.LOCKED_BY_OTHER, owner="sam")
    first = env.run(hooks.after_save(env.blend))
    assert kinds(first) == [("Message", "Lore - File Locked by Another User")]
    assert "sam" in first[0].text
    assert kinds(env.run(hooks.after_save(env.blend))) == []


def test_unknown_lock_state_warns_once(env, monkeypatch) -> None:
    monkeypatch.setattr(host, "has_ui", lambda: True)
    env.session.state.update(lock=LockState.UNKNOWN)
    assert kinds(env.run(hooks.after_save(env.blend))) == [("Message", "Lore - Lock State Unknown")]
    assert kinds(env.run(hooks.after_save(env.blend))) == []


def test_after_save_offers_to_stage_a_new_file(env) -> None:
    env.session.state["tracked"] = False
    env.session.returns["is_staged"] = False
    shown = env.run(hooks.after_save(env.blend), answers=["Stage"])
    assert kinds(shown) == [("Ask", "Lore - Stage New File?")]
    assert ("set_references", [env.blend, env.scene.inside]) in env.session.calls
    assert ("stage", [env.blend]) in env.session.calls


def test_stage_prompt_skips_staged_files_backups_and_the_pref(env, tmp_path) -> None:
    env.session.state["tracked"] = False
    env.session.returns["is_staged"] = True
    assert kinds(env.run(hooks.after_save(env.blend))) == []
    env.session.returns["is_staged"] = False
    backup = tmp_path / "shot.blend1"
    backup.write_text("x")
    assert kinds(env.run(hooks.after_save(str(backup)))) == []
    host.save_prefs(host.load_prefs().__class__(prompt_stage_after_save=False))
    assert kinds(env.run(hooks.after_save(env.blend))) == []


def test_after_load_offers_sync_only_when_behind(env) -> None:
    env.session.returns["behind"] = BehindReport(False, ())
    assert kinds(env.run(hooks.after_load(env.blend))) == []
    assert "invalidate" in env.session.names()
    env.session.returns["behind"] = BehindReport(False, ("tex/a.png",))
    shown = env.run(hooks.after_load(env.blend), answers=[None])
    assert kinds(shown) == [("Ask", "Lore - Dependencies Behind Server")]
    assert "tex/a.png" in shown[0].text


def test_after_load_ignores_a_file_outside_a_working_copy(env, monkeypatch) -> None:
    def outside(path):
        raise NotAWorkingCopy(str(path))

    monkeypatch.setattr(host, "session_for", outside)
    assert kinds(env.run(hooks.after_load(env.blend))) == []


def test_handlers_start_flows_only_when_enabled(env, monkeypatch) -> None:
    started: list[object] = []
    monkeypatch.setattr(host, "start", started.append)
    hooks.on_save_post(env.blend, None)
    monkeypatch.setattr(bpy.data, "filepath", "")
    hooks.on_load_post("", None)
    assert len(started) == 1
    host.save_prefs(host.load_prefs().__class__(enabled=False))
    hooks.on_save_post(env.blend, None)
    assert len(started) == 1

