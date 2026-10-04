"""Decision logic of the save hooks and action summaries, with a fake host and session."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from lore_dcc.errors import NotAWorkingCopy
from lore_dcc.houdini import actions, hooks, host
from lore_dcc.locks import LockState
from lore_dcc.prefs import Prefs
from lore_dcc.session import FileState, WriteResult


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.state = {"exists": True, "tracked": True, "lock": LockState.NOT_LOCKED, "owner": None}
        self.returns: dict[str, object] = {}
        self.workspace = SimpleNamespace(relative=lambda p: Path(p).name)

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
def env(monkeypatch: pytest.MonkeyPatch):
    session = FakeSession()
    answers: list[str | None] = []
    asked: list[tuple[str, list[str]]] = []
    state = SimpleNamespace(session=session, prefs=Prefs(), asked=asked, answers=answers, ui=True)

    def ask(title, text, buttons, default=None):
        asked.append((title, list(buttons)))
        return answers.pop(0) if answers else None

    monkeypatch.setattr(host, "load_prefs", lambda: state.prefs)
    monkeypatch.setattr(host, "session_for", lambda path: state.session)
    monkeypatch.setattr(host, "run", lambda fn, *args, title="Lore": fn(*args))
    monkeypatch.setattr(host, "has_ui", lambda: state.ui)
    monkeypatch.setattr(host, "ask", ask)
    monkeypatch.setattr(host, "message", lambda title, text: asked.append((title, [])))
    monkeypatch.setattr(host, "error", lambda exc, context="": asked.append(("error", [])))
    monkeypatch.setattr(hooks, "_unknown_warned", False)
    monkeypatch.setattr(hooks, "_save_unlocked", set())
    monkeypatch.setattr(hooks, "_read_only_warned", set())
    return state


def test_lock_prompt_offers_two_choices_and_locks(env) -> None:
    env.answers.append("Lock and save")
    hooks.before_save("a.hip")
    assert env.asked == [("Lock and save?", ["Lock and save", "Save without lock"])]
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
    assert [title for title, _ in env.asked] == ["Lock and save?"]


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
    assert [title for title, _ in env.asked] == ["Lock and save?", "Lock and save?"]
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
    assert env.asked == [("Lore", [])]
    hooks.before_save(str(other))
    assert env.asked == [("Lore", []), ("Lore", [])]
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
    assert env.asked == [("Locked by someone else", ["Make writable", "Leave read-only"])]
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
    assert [title for title, _ in env.asked] == ["Lore"]
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
    hooks.after_save("a.hip")
    assert env.asked == [("Stage it?", ["Stage", "Not now"])]
    assert env.session.names() == ["mark_saved", "file_states", "is_staged", "stage"]


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
    hooks.after_load("a.hip")
    assert env.session.calls == [("invalidate", []), ("refresh_read_only", ["a.hip"])]
    assert env.asked == []


def test_library_file_ignores_embedded_and_missing_definitions() -> None:
    assert hooks.library_file(None) is None
    assert hooks.library_file(SimpleNamespace(libraryFilePath=lambda: "Embedded")) is None
    assert hooks.library_file(SimpleNamespace(libraryFilePath=lambda: "")) is None
    absolute = os.path.abspath("lib.hda")
    assert hooks.library_file(SimpleNamespace(libraryFilePath=lambda: absolute)) == absolute


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
    assert [title for title, _ in env.asked] == ["Lore", "Lore"]
    assert env.session.calls == []


def test_parm_actions_refuse_a_folder(env, parms, tmp_path: Path) -> None:
    actions.lock_parm_file(FakeParm(str(tmp_path)))
    actions.discard_parm_file(FakeParm(str(tmp_path)))
    assert [title for title, _ in env.asked] == ["Lore", "Lore"]
    assert env.session.calls == []


def test_discard_parm_requires_an_existing_file_but_lock_does_not(env, parms, tmp_path: Path) -> None:
    missing = str(tmp_path / "gone.bgeo")
    actions.discard_parm_file(FakeParm(missing))
    assert env.asked == [("Lore", [])]
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
    assert [title for title, _ in env.asked] == ["Discard changes"]


def test_discard_refuses_a_new_file_without_asking(env, tmp_path: Path) -> None:
    env.session.state.update(tracked=False)
    target = str(tmp_path / "new.hip")
    assert actions._discard_files(env.session, [target], "") is False
    assert env.asked == [("Lore", [])]
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
    assert env.asked == [("Lore", [])]
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
    assert env.asked == [("Lore", [])]
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
