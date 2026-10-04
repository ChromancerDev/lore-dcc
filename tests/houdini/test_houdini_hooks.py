"""Decision logic of the save hooks and action summaries, with a fake host and session."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from lore_dcc.errors import NotAWorkingCopy
from lore_dcc.houdini import actions, hooks, host, references
from lore_dcc.locks import LockState
from lore_dcc.prefs import Prefs
from lore_dcc.session import BehindReport, FileState, SyncResult, WriteResult
from lore_dcc.ui import TITLE_PREFIX


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.state = {"exists": True, "tracked": True, "lock": LockState.NOT_LOCKED, "owner": None}
        self.returns: dict[str, object] = {}
        self.workspace = SimpleNamespace(relative=lambda p: Path(p).name, root=Path("/work"))

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
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    session = FakeSession()
    answers: list[str | None] = []
    asked: list[tuple[str, list[str]]] = []
    texts: list[str] = []
    hip = tmp_path / "scene.hip"
    hip.write_text("x")
    state = SimpleNamespace(
        session=session,
        prefs=Prefs(),
        asked=asked,
        answers=answers,
        ui=True,
        texts=texts,
        messages=[],
        hip=str(hip),
        unsaved=False,
        loaded=[],
        reloaded=[],
        scene=references.SceneReferences((Path("/work/geo/a.bgeo"),), ()),
    )
    monkeypatch.setattr(
        hooks.hou,
        "hipFile",
        SimpleNamespace(
            path=lambda: state.hip,
            hasUnsavedChanges=lambda: state.unsaved,
            load=lambda f, **kw: state.loaded.append(f),
        ),
        raising=False,
    )
    monkeypatch.setattr(references, "scan", lambda root: state.scene)
    monkeypatch.setattr(references, "reload", lambda paths: state.reloaded.append(list(paths)))

    def ask(title, text, buttons, default=None):
        assert title.startswith(TITLE_PREFIX), title
        asked.append((title, list(buttons)))
        texts.append(text)
        return answers.pop(0) if answers else None

    monkeypatch.setattr(host, "load_prefs", lambda: state.prefs)
    monkeypatch.setattr(host, "session_for", lambda path: state.session)
    monkeypatch.setattr(host, "run", lambda fn, *args, title="Lore": fn(*args))
    monkeypatch.setattr(host, "has_ui", lambda: state.ui)
    monkeypatch.setattr(host, "ask", ask)

    def message(title, text):
        assert title.startswith(TITLE_PREFIX), title
        asked.append((title, []))
        state.messages.append(text)

    monkeypatch.setattr(host, "message", message)
    monkeypatch.setattr(host, "error", lambda exc, context="": asked.append(("error", [])))
    monkeypatch.setattr(hooks, "_unknown_warned", False)
    monkeypatch.setattr(hooks, "_save_unlocked", set())
    monkeypatch.setattr(hooks, "_read_only_warned", set())
    return state


def test_lock_prompt_offers_two_choices_and_locks(env) -> None:
    env.answers.append("Lock and save")
    hooks.before_save("a.hip")
    assert env.asked == [("Lore - Lock File?", ["Lock and save", "Save without lock"])]
    assert env.session.names() == ["file_states", "lock"]


def test_save_without_lock_makes_writable_and_is_remembered_per_file(env, tmp_path: Path) -> None:
    target = tmp_path / "a.hip"
    target.write_text("x")
    other = tmp_path / "b.hip"
    other.write_text("x")
    env.answers.append("Save without lock")
    hooks.before_save(str(target))
    assert env.session.names() == ["file_states", "make_writable"]
    env.session.calls.clear()
    env.asked.clear()
    hooks.before_save(str(target))
    assert env.asked == []
    assert env.session.names() == ["file_states"]
    env.answers.append("Lock and save")
    hooks.before_save(str(other))
    assert [title for title, _ in env.asked] == ["Lore - Lock File?"]


def test_remembered_save_without_lock_rewrites_a_read_only_bit_silently(env, tmp_path: Path) -> None:
    target = tmp_path / "a.hip"
    target.write_text("x")
    env.answers.append("Save without lock")
    hooks.before_save(str(target))
    os.chmod(target, 0o444)
    env.session.calls.clear()
    env.asked.clear()
    hooks.before_save(str(target))
    assert env.asked == []
    assert env.session.names() == ["file_states", "make_writable"]
    os.chmod(target, 0o666)


def test_closing_the_lock_prompt_changes_nothing_and_asks_again_next_time(env) -> None:
    hooks.before_save("a.hip")
    hooks.before_save("a.hip")
    assert [title for title, _ in env.asked] == ["Lore - Lock File?", "Lore - Lock File?"]
    assert env.session.names() == ["file_states", "file_states"]


def test_prompt_off_warns_once_per_file_when_read_only(env, tmp_path: Path, caplog) -> None:
    env.prefs = Prefs(prompt_lock_before_save=False)
    target = tmp_path / "a.hip"
    target.write_text("x")
    os.chmod(target, 0o444)
    other = tmp_path / "b.hip"
    other.write_text("x")
    os.chmod(other, 0o444)
    with caplog.at_level("WARNING", logger="lore_dcc.houdini.hooks"):
        hooks.before_save(str(target))
        hooks.before_save(str(target))
    assert len([r for r in caplog.records if "will fail" in r.getMessage()]) == 2
    assert env.asked == [("Lore - File Not Locked", [])]
    hooks.before_save(str(other))
    assert env.asked == [("Lore - File Not Locked", []), ("Lore - File Not Locked", [])]
    assert env.session.names() == ["file_states"] * 3
    os.chmod(target, 0o666)
    os.chmod(other, 0o666)


def test_prompt_off_stays_quiet_for_writable_files_and_without_a_ui(env, tmp_path: Path) -> None:
    env.prefs = Prefs(prompt_lock_before_save=False)
    writable = tmp_path / "a.hip"
    writable.write_text("x")
    hooks.before_save(str(writable))
    assert env.asked == []
    readonly = tmp_path / "b.hip"
    readonly.write_text("x")
    os.chmod(readonly, 0o444)
    env.ui = False
    hooks.before_save(str(readonly))
    assert env.asked == []
    os.chmod(readonly, 0o666)


def test_untracked_and_own_locks_never_prompt(env) -> None:
    env.session.state.update(tracked=False)
    hooks.before_save("a.hip")
    env.session.state.update(tracked=True, lock=LockState.LOCKED_BY_ME)
    hooks.before_save("a.hip")
    assert env.asked == []
    assert env.session.names() == ["file_states", "file_states"]


def test_locked_by_other_names_the_owner_and_offers_writable(env, tmp_path: Path) -> None:
    target = tmp_path / "a.hip"
    target.write_text("x")
    os.chmod(target, 0o444)
    env.session.state.update(lock=LockState.LOCKED_BY_OTHER, owner="Ana")
    env.answers.append("Make writable")
    hooks.before_save(str(target))
    assert env.asked == [("Lore - File Locked by Another User", ["Make writable", "Leave read-only"])]
    assert env.session.names() == ["file_states", "make_writable"]
    os.chmod(target, 0o666)


def test_locked_by_other_does_not_ask_again_once_writable(env, tmp_path: Path) -> None:
    target = tmp_path / "a.hip"
    target.write_text("x")
    env.session.state.update(lock=LockState.LOCKED_BY_OTHER, owner="Ana")
    hooks.before_save(str(target))
    assert env.asked == []


def test_unknown_state_warns_once_and_changes_nothing(env) -> None:
    env.session.state.update(lock=LockState.UNKNOWN)
    hooks.before_save("a.hip")
    hooks.before_save("a.hip")
    assert [title for title, _ in env.asked] == ["Lore - Lock State Unknown"]
    assert env.session.names() == ["file_states", "file_states"]


def test_without_a_ui_prompts_are_skipped(env) -> None:
    env.ui = False
    hooks.before_save("a.hip")
    hooks.after_save("a.hip")
    assert env.session.names() == ["file_states", "mark_saved", "file_states"]


def test_disabled_hooks_do_nothing(env) -> None:
    env.prefs = Prefs(enabled=False)
    hooks.before_save("a.hip")
    hooks.after_save("a.hip")
    hooks.after_load("a.hip")
    assert env.session.calls == []


def test_not_a_working_copy_is_silent(env, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(path):
        raise NotAWorkingCopy("no")

    monkeypatch.setattr(host, "session_for", refuse)
    hooks.before_save("a.hip")
    hooks.after_save("a.hip")
    hooks.after_load("a.hip")
    assert env.asked == []


def test_after_save_marks_then_offers_to_stage_a_new_file(env) -> None:
    env.session.state.update(tracked=False)
    env.answers.append("Stage")
    hooks.after_save(env.hip)
    assert env.asked == [("Lore - Stage New File?", ["Stage", "Not now"])]
    assert env.session.names() == [
        "mark_saved", "file_states", "is_staged", "set_references", "stage",
    ]  # fmt: skip
    assert env.session.calls[3] == ("set_references", [env.hip, env.scene.inside])


def test_staging_another_file_records_no_references(env, tmp_path: Path) -> None:
    env.session.state.update(tracked=False)
    env.answers.append("Stage")
    hooks.after_save(str(tmp_path / "tool.hda"))
    assert "set_references" not in env.session.names()
    assert "stage" in env.session.names()


def test_staging_the_hip_warns_about_files_outside_the_repo(env) -> None:
    env.scene = references.SceneReferences((), (("/obj/a:file", "D:/ext/a.bgeo"),))
    env.session.state.update(tracked=False)
    env.answers.append("Stage")
    hooks.after_save(env.hip)
    assert [t for t, _ in env.asked] == [
        "Lore - Stage New File?",
        "Lore - References Outside the Repo",
    ]
    assert "/obj/a:file: D:/ext/a.bgeo" in env.messages[0]
    assert env.session.names()[-1] == "stage"


def test_a_failing_scan_does_not_stop_staging(env, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(root):
        raise RuntimeError("scan")

    monkeypatch.setattr(references, "scan", boom)
    env.session.state.update(tracked=False)
    env.answers.append("Stage")
    hooks.after_save(env.hip)
    assert env.session.names()[-1] == "stage"


def test_after_save_does_not_ask_when_the_new_file_is_already_staged(env) -> None:
    env.session.state.update(tracked=False)
    env.session.returns["is_staged"] = True
    hooks.after_save("a.hip")
    assert env.asked == []
    assert env.session.names() == ["mark_saved", "file_states", "is_staged"]


def test_after_save_skips_staging_for_tracked_locked_or_declined(env) -> None:
    hooks.after_save("a.hip")
    env.session.state.update(tracked=False, lock=LockState.LOCKED_BY_ME)
    hooks.after_save("a.hip")
    assert env.asked == []
    env.session.state.update(lock=LockState.NOT_LOCKED)
    env.answers.append("Not now")
    hooks.after_save("a.hip")
    assert "stage" not in env.session.names()
    env.prefs = Prefs(prompt_stage_after_save=False)
    env.asked.clear()
    hooks.after_save("a.hip")
    assert env.asked == []


def test_after_load_drops_cached_identity_then_refreshes_the_read_only_bit(env) -> None:
    env.ui = False
    hooks.after_load("a.hip")
    assert env.session.calls == [("invalidate", []), ("refresh_read_only", ["a.hip"])]
    assert env.asked == []


@pytest.fixture
def background(env, monkeypatch: pytest.MonkeyPatch):
    tasks = pytest.importorskip("lore_dcc.ui.tasks")
    started: list[tuple[object, tuple, object]] = []
    monkeypatch.setattr(
        tasks, "run_in_background", lambda fn, *a, on_done: started.append((fn, a, on_done))
    )
    synced = []
    monkeypatch.setattr(actions, "sync_dependencies", lambda confirm=True: synced.append(confirm))
    return SimpleNamespace(started=started, synced=synced)


def test_after_load_records_references_and_checks_the_server_in_the_background(
    env, background
) -> None:
    hooks.after_load(env.hip)
    assert env.session.names() == ["invalidate", "refresh_read_only", "set_references"]
    assert env.session.calls[2] == ("set_references", [env.hip, env.scene.inside])
    ((fn, args, _),) = background.started
    fn(*args)
    assert env.session.calls[-1] == ("behind", [env.hip])
    assert env.asked == []


def test_after_load_offers_a_sync_when_something_is_behind(env, background) -> None:
    hooks.after_load(env.hip)
    env.answers.append("Sync Dependencies")
    background.started[0][2](BehindReport(True, ("geo/a.bgeo",)), None)
    assert env.asked == [("Lore - Dependencies Behind Server", ["Sync Dependencies", "Not now"])]
    assert "scene itself is behind" in env.texts[0] and "geo/a.bgeo" in env.texts[0]
    assert background.synced == [False]
    env.answers.append("Not now")
    background.started[0][2](BehindReport(False, ("geo/a.bgeo",)), None)
    assert background.synced == [False]


def test_after_load_result_is_dropped_on_error_nothing_behind_or_another_scene(
    env, background, caplog
) -> None:
    hooks.after_load(env.hip)
    done = background.started[0][2]
    with caplog.at_level("WARNING", logger="lore_dcc.houdini.hooks"):
        done(None, RuntimeError("offline"))
    assert "offline" in caplog.text
    done(BehindReport(False, ()), None)
    env.hip = "other.hip"
    done(BehindReport(True, ("geo/a.bgeo",)), None)
    assert env.asked == []


def test_after_load_without_a_ui_skips_the_reference_check(env, background) -> None:
    env.ui = False
    hooks.after_load(env.hip)
    assert background.started == []
    assert "set_references" not in env.session.names()


def test_describe_write_lists_each_group_and_truncates() -> None:
    result = WriteResult(
        staged=("a.bgeo",), changed=tuple(f"c{i}.bgeo" for i in range(10)), released=()
    )
    text = actions.describe_write(result)
    assert "Staged (new): 1" in text
    assert "Changed (lock kept): 10" in text
    assert "and 2 more" in text
    assert "Released" not in text
    assert actions.describe_write(WriteResult((), (), ())) == "No output files changed."


class FakeParm:
    def __init__(self, value: str, name: str = "file") -> None:
        self.value = value
        self._name = name

    def evalAsString(self) -> str:
        return self.value

    def name(self) -> str:
        return self._name


@pytest.fixture
def parms(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        actions.hou, "text", SimpleNamespace(expandString=lambda value: value), raising=False
    )


@pytest.mark.parametrize("value", ["", "   "])
def test_parm_actions_refuse_an_empty_value(env, parms, value: str) -> None:
    actions.lock_parm_file(FakeParm(value))
    actions.discard_parm_file(FakeParm(value))
    assert [title for title, _ in env.asked] == ["Lore - Parameter File", "Lore - Parameter File"]
    assert env.session.calls == []


def test_parm_actions_refuse_a_folder(env, parms, tmp_path: Path) -> None:
    actions.lock_parm_file(FakeParm(str(tmp_path)))
    actions.discard_parm_file(FakeParm(str(tmp_path)))
    assert [title for title, _ in env.asked] == ["Lore - Parameter File", "Lore - Parameter File"]
    assert env.session.calls == []


def test_discard_parm_requires_an_existing_file_but_lock_does_not(env, parms, tmp_path: Path) -> None:
    missing = str(tmp_path / "gone.bgeo")
    actions.discard_parm_file(FakeParm(missing))
    assert env.asked == [("Lore - Parameter File", [])]
    assert env.session.calls == []
    actions.lock_parm_file(FakeParm(missing))
    assert env.session.names() == ["lock"]


def test_parm_actions_expand_variables_and_run_on_a_file(env, monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "a.bgeo"
    target.write_text("x")
    monkeypatch.setattr(
        actions.hou,
        "text",
        SimpleNamespace(expandString=lambda value: value.replace("$HIP", str(tmp_path))),
        raising=False,
    )
    env.answers.append("Discard changes")
    actions.discard_parm_file(FakeParm("$HIP/a.bgeo"))
    assert env.session.names() == ["file_states", "discard"]
    assert [title for title, _ in env.asked] == ["Lore - Discard Changes?"]


def test_discard_refuses_a_new_file_without_asking(env, tmp_path: Path) -> None:
    env.session.state.update(tracked=False)
    target = str(tmp_path / "new.hip")
    assert actions._discard_files(env.session, [target], "") is False
    assert env.asked == [("Lore - Discard Changes", [])]
    assert env.session.names() == ["file_states"]


def test_discard_message_names_the_file_and_the_reason(env, monkeypatch, tmp_path: Path) -> None:
    env.session.state.update(tracked=False)
    messages = []
    monkeypatch.setattr(host, "message", lambda title, text: messages.append(text))
    actions._discard_files(env.session, [str(tmp_path / "new.hip")], "")
    assert messages == ["new.hip is new. Lore has no committed version to go back to."]


def test_discard_hda_refuses_a_new_library_file(env, monkeypatch, tmp_path: Path) -> None:
    env.session.state.update(tracked=False)
    reloaded = []
    monkeypatch.setattr(actions, "_library_file", lambda node: str(tmp_path / "lib.hda"))
    monkeypatch.setattr(actions.hou, "hda", SimpleNamespace(reloadFile=reloaded.append), raising=False)
    actions.discard_hda(object())
    assert env.asked == [("Lore - Discard Changes", [])]
    assert reloaded == []
    assert "discard" not in env.session.names()


def test_discard_hip_refuses_a_new_file_and_reloads_a_tracked_one(
    env, monkeypatch, tmp_path: Path
) -> None:
    hip = tmp_path / "scene.hip"
    hip.write_text("x")
    loaded = []
    monkeypatch.setattr(
        actions.hou,
        "hipFile",
        SimpleNamespace(path=lambda: str(hip), load=lambda f, **kw: loaded.append(f)),
        raising=False,
    )
    env.session.state.update(tracked=False)
    actions.discard()
    assert loaded == []
    assert env.asked == [("Lore - Discard Changes", [])]
    env.asked.clear()
    env.session.state.update(tracked=True)
    env.answers.append("Discard changes")
    actions.discard()
    assert loaded == [str(hip)]
    assert env.session.names()[-1] == "discard"


def lock_outputs(env, monkeypatch, paths, notes=()):
    def fake_outputs(node, session, found_notes=None):
        found_notes.extend(notes)
        return list(paths)

    monkeypatch.setattr(actions.outputs, "outputs_for_node", fake_outputs)
    monkeypatch.setattr(actions, "_hip", lambda: "scene.hip")
    messages = []
    monkeypatch.setattr(host, "message", lambda title, text: messages.append(text))
    actions.lock_node_outputs(object())
    return messages


def test_lock_outputs_says_already_locked_when_every_tracked_file_is_mine(env, monkeypatch) -> None:
    env.session.state.update(lock=LockState.LOCKED_BY_ME)
    env.session.returns["prepare_write"] = ()
    messages = lock_outputs(env, monkeypatch, ["a.bgeo"])
    assert messages == ["Already locked by you."]


def test_lock_outputs_says_nothing_to_lock_for_new_files(env, monkeypatch) -> None:
    env.session.state.update(tracked=False)
    env.session.returns["prepare_write"] = ()
    messages = lock_outputs(env, monkeypatch, ["a.bgeo"])
    assert messages == ["Nothing to lock. New files need no lock."]


def test_lock_outputs_summary_carries_frame_cap_warnings(env, monkeypatch) -> None:
    env.session.returns["prepare_write"] = ("a.bgeo",)
    messages = lock_outputs(env, monkeypatch, ["a.bgeo"], notes=["/obj/rop renders 200,000 frames"])
    assert messages == ["Locked 1 output file(s).\nWarning: /obj/rop renders 200,000 frames"]


def test_collect_node_outputs_summary_carries_frame_cap_warnings(env, monkeypatch) -> None:
    def fake_outputs(node, session, found_notes=None):
        found_notes.append("capped")
        return []

    monkeypatch.setattr(actions.outputs, "outputs_for_node", fake_outputs)
    monkeypatch.setattr(actions, "_hip", lambda: "scene.hip")
    messages = []
    monkeypatch.setattr(host, "message", lambda title, text: messages.append(text))
    actions.collect_node_outputs(object())
    assert messages == ["This scene writes no files inside the working copy.\nWarning: capped"]


def test_stage_prompt_wording(env) -> None:
    env.session.state.update(tracked=False)
    hooks.after_save("D:/work/scene.hip")
    assert env.asked == [("Lore - Stage New File?", ["Stage", "Not now"])]
    assert env.texts == ["scene.hip is not staged in Lore. Stage it now?"]


def test_lock_prompt_wording(env) -> None:
    hooks.before_save("D:/work/scene.hip")
    assert env.asked == [("Lore - Lock File?", ["Lock and save", "Save without lock"])]
    assert env.texts == ["scene.hip is not locked in Lore. Lock it before saving?"]


def test_locked_by_another_user_wording(env, tmp_path: Path) -> None:
    target = tmp_path / "scene.hip"
    target.write_text("x")
    os.chmod(target, 0o444)
    env.session.state.update(lock=LockState.LOCKED_BY_OTHER, owner="Ana")
    hooks.before_save(str(target))
    assert env.asked == [
        ("Lore - File Locked by Another User", ["Make writable", "Leave read-only"])
    ]
    assert env.texts == [
        (
            "scene.hip is locked in Lore by Ana. Saving here makes a local copy you cannot "
            "commit until they release the lock."
        )
    ]
    os.chmod(target, 0o666)


def sync(env, *, behind, result=None, edited=False, answers=()):
    env.session.returns["behind"] = behind
    env.session.returns["sync_references"] = result
    env.session.lore = SimpleNamespace(
        check_dirty=lambda paths: SimpleNamespace(
            staged=(), unstaged=(SimpleNamespace(path="scene.hip"),) if edited else ()
        )
    )
    env.answers.extend(answers)
    actions.sync_dependencies()


def test_sync_dependencies_with_nothing_behind_only_says_so(env) -> None:
    sync(env, behind=BehindReport(False, ()))
    assert env.session.names() == ["set_references", "behind"]
    assert env.asked == [("Lore - Sync Dependencies", [])]
    assert env.messages == ["Every reference is up to date with the server."]


def test_sync_dependencies_syncs_behind_references_and_reloads_them(env) -> None:
    result = SyncResult(("geo/a.bgeo", "tex/b.exr"), ())
    behind = BehindReport(False, ("geo/a.bgeo", "tex/b.exr"))
    sync(env, behind=behind, result=result, answers=["Sync"])
    assert env.asked[0] == ("Lore - Sync Dependencies?", ["Sync", "Cancel"])
    assert env.session.calls[-1] == ("sync_references", ["geo/a.bgeo", "tex/b.exr"])
    assert env.reloaded == [[Path("/work/geo/a.bgeo"), Path("/work/tex/b.exr")]]
    assert env.loaded == []
    assert "Synced 2 file(s)" in env.messages[-1]


def test_sync_dependencies_cancel_syncs_nothing(env) -> None:
    sync(env, behind=BehindReport(False, ("geo/a.bgeo",)), answers=["Cancel"])
    assert "sync_references" not in env.session.names()


def test_sync_dependencies_reports_skipped_files(env) -> None:
    result = SyncResult(("geo/a.bgeo",), ("tex/b.exr",))
    behind = BehindReport(False, ("geo/a.bgeo", "tex/b.exr"))
    sync(env, behind=behind, result=result, answers=["Sync"])
    assert "tex/b.exr" in env.messages[-1]
    assert "Committing it writes over the newer version" in env.messages[-1]


def test_behind_scene_offers_reopen_and_warns_about_overwriting(env) -> None:
    result = SyncResult(("geo/a.bgeo", "scene.hip"), ())
    behind = BehindReport(True, ("geo/a.bgeo",))
    sync(env, behind=behind, result=result, answers=["Sync and reopen"])
    assert env.asked[0] == (
        "Lore - Scene Behind Server",
        ["Continue", "Sync and reopen", "Cancel"],
    )
    assert "behind the server" in env.texts[0] and "overwrite" in env.texts[0]
    assert env.session.calls[-1] == ("sync_references", ["geo/a.bgeo", "scene.hip"])
    assert env.reloaded == [[Path("/work/geo/a.bgeo")]]
    assert env.loaded == [env.hip]


def test_reopen_with_unsaved_changes_asks_to_discard_them(env) -> None:
    env.unsaved = True
    sync(env, behind=BehindReport(True, ()), answers=["Sync and reopen", "Cancel"])
    assert [t for t, _ in env.asked] == [
        "Lore - Scene Behind Server",
        "Lore - Discard Unsaved Changes?",
    ]
    assert env.asked[1][1] == ["Sync and reopen", "Cancel"]
    assert "sync_references" not in env.session.names()
    assert env.loaded == []


def test_continue_syncs_only_references_and_does_not_reopen(env) -> None:
    result = SyncResult(("geo/a.bgeo",), ())
    sync(env, behind=BehindReport(True, ("geo/a.bgeo",)), result=result, answers=["Continue"])
    assert env.session.calls[-1] == ("sync_references", ["geo/a.bgeo"])
    assert env.loaded == []


def test_continue_with_no_behind_references_has_nothing_to_sync(env) -> None:
    sync(env, behind=BehindReport(True, ()), answers=["Continue"])
    assert "sync_references" not in env.session.names()
    assert env.asked[-1][0] == "Lore - Sync Dependencies"


def test_a_scene_with_local_edits_cannot_be_reopened(env) -> None:
    result = SyncResult(("geo/a.bgeo",), ())
    behind = BehindReport(True, ("geo/a.bgeo",))
    sync(env, behind=behind, result=result, edited=True, answers=["Continue"])
    assert env.asked[0] == ("Lore - Scene Behind Server", ["Continue", "Cancel"])
    assert "local changes" in env.texts[0]
    assert env.session.calls[-1] == ("sync_references", ["geo/a.bgeo"])


def test_a_skipped_scene_is_reported_and_not_reopened(env) -> None:
    result = SyncResult(("geo/a.bgeo",), ("scene.hip",))
    behind = BehindReport(True, ("geo/a.bgeo",))
    sync(env, behind=behind, result=result, answers=["Sync and reopen"])
    assert env.loaded == []
    assert "The scene was not synced or reopened." in env.messages[-1]


def test_long_lists_are_capped(env) -> None:
    names = tuple(f"geo/{i}.bgeo" for i in range(12))
    sync(env, behind=BehindReport(False, names), answers=["Cancel"])
    assert "and 4 more" in env.texts[0]


def test_warn_outside_lists_where_and_file_and_only_warns(env) -> None:
    actions.warn_outside(references.SceneReferences((), ()))
    assert env.asked == []
    actions.warn_outside(references.SceneReferences((), (("Sop/ext", "D:/ext/a.hda"),)))
    assert env.asked == [("Lore - References Outside the Repo", [])]
    assert "Sop/ext: D:/ext/a.hda" in env.messages[0]
    assert "other users will not get" in env.messages[0] and "still commits" in env.messages[0]


def test_commit_records_references_and_warns_before_the_dialog(env, monkeypatch) -> None:
    dialogs = pytest.importorskip("lore_dcc.ui.dialogs")
    env.scene = references.SceneReferences((), (("Sop/ext", "D:/ext/a.hda"),))
    shown = []
    monkeypatch.setattr(dialogs, "show_commit", lambda session, prefs: shown.append(session))
    actions.commit()
    assert env.session.names() == ["set_references"]
    assert [t for t, _ in env.asked] == ["Lore - References Outside the Repo"]
    assert shown == [env.session]
