"""Pure-Python tests for the output table, frame lists and path filtering."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import hou
import pytest
from lore_dcc.errors import SettingsError
from lore_dcc.houdini import outputs
from lore_dcc.houdini.outputs import OutputSpec, find_spec


def test_table_keys_strip_the_version_and_try_the_category_first() -> None:
    assert outputs.table_keys("Sop/filecache::2.0") == (
        "Sop/filecache::2.0",
        "Sop/filecache",
        "filecache::2.0",
        "filecache",
    )
    assert outputs.table_keys("Sop/kinefx::rop_fbxanimoutput") == (
        "Sop/kinefx::rop_fbxanimoutput",
        "kinefx::rop_fbxanimoutput",
    )
    assert outputs.table_keys("Driver/karma") == ("Driver/karma", "karma")


def test_default_table_is_category_qualified_and_nonempty() -> None:
    assert outputs.DEFAULT_OUTPUTS
    for key, spec in outputs.DEFAULT_OUTPUTS.items():
        assert key.split("/")[0] in {"Sop", "Driver", "Lop", "Cop"}, key
        assert spec.parms and all(spec.parms), key


def test_find_matches_versioned_types_and_keeps_categories_apart() -> None:
    assert find_spec({}, "Sop/filecache::2.0").parms == ("sopoutput",)
    assert find_spec({}, "Lop/filecache").parms == ("lopoutput",)
    assert find_spec({}, "Driver/gltf::2.0").parms == ("outputfile",)
    assert find_spec({}, "Cop/file") is None
    assert find_spec({}, "Sop/gltf::2.0") is None


def test_file_sop_only_counts_in_write_mode() -> None:
    spec = find_spec({}, "Sop/file")
    assert spec.parms == ("file",)
    assert spec.when == (("filemode", "write"),)


def test_overrides_win_over_defaults_and_replace_the_whole_entry() -> None:
    overrides = outputs.parse_overrides(
        {
            "filecache": {"parms": ["file"], "when": {"filemethod": 1}},
            "Sop/mylib::exporter": {"parms": ["out"]},
        }
    )
    assert find_spec(overrides, "Sop/filecache::2.0") == OutputSpec(("file",), (("filemethod", 1),))
    assert find_spec(overrides, "Lop/filecache").parms == ("file",)
    assert find_spec(overrides, "Sop/mylib::exporter::1.0").parms == ("out",)
    assert find_spec(overrides, "Sop/rop_geometry").parms == ("sopoutput",)


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"": {"parms": ["a"]}},
        {"Sop/x": []},
        {"Sop/x": {}},
        {"Sop/x": {"parms": []}},
        {"Sop/x": {"parms": [1]}},
        {"Sop/x": {"parms": "a"}},
        {"Sop/x": {"parms": ["a"], "extra": 1}},
        {"Sop/x": {"parms": ["a"], "when": []}},
        {"Sop/x": {"parms": ["a"], "when": {"m": 1.5}}},
        {"Sop/x": {"parms": ["a"], "when": {"m": True}}},
    ],
)
def test_parse_overrides_rejects_bad_shapes(raw: object) -> None:
    with pytest.raises(SettingsError):
        outputs.parse_overrides(raw)


def test_parse_overrides_accepts_none_and_empty() -> None:
    assert outputs.parse_overrides(None) == {}
    assert outputs.parse_overrides({}) == {}


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((1, 3, 1), [1, 2, 3]),
        ((5, 9, 2), [5, 7, 9]),
        ((1, 10, 4), [1, 5, 9]),
        ((1, 2, 0.5), [1, 1.5, 2]),
        ((4, 4, 1), [4]),
        ((1, 3, 0), [1, 2, 3]),
        ((1, 3, -2), [1, 2, 3]),
        ((9, 5, 1), []),
        ((0, 0.3, 0.1), [0, 0.1, 0.2, 0.3]),
    ],
)
def test_frame_range_frames(args: tuple[float, float, float], expected: list[float]) -> None:
    frames, total = outputs.frame_range(*args)
    assert frames == pytest.approx(expected)
    assert total == len(expected)


def test_frame_range_is_capped_but_counts_every_frame() -> None:
    frames, total = outputs.frame_range(1, 10**9, 1)
    assert (len(frames), total) == (100_000, 10**9)


def test_when_matches_compares_ints_to_values_and_strings_to_tokens() -> None:
    values = {("filemode", False): 2, ("filemode", True): "write"}

    def read(name: str, token: bool) -> object:
        return values.get((name, token))

    assert outputs.when_matches([("filemode", 2)], read)
    assert outputs.when_matches([("filemode", "write")], read)
    assert not outputs.when_matches([("filemode", 1)], read)
    assert not outputs.when_matches([("filemode", "read")], read)
    assert not outputs.when_matches([("missing", 0)], read)
    assert outputs.when_matches([], read)


def test_evaluate_paths_evaluates_a_static_parm_once() -> None:
    calls: list[float] = []

    def eval_at(frame: float) -> str:
        calls.append(frame)
        return "a.abc"

    assert outputs.evaluate_paths(eval_at, False, [1, 2, 3]) == ["a.abc"]
    assert calls == [1]


def test_evaluate_paths_dedupes_and_keeps_order() -> None:
    result = outputs.evaluate_paths(lambda f: f"x.{int(f) // 2}", True, [1, 2, 3, 4, 5])
    assert result == ["x.0", "x.1", "x.2"]
    assert outputs.evaluate_paths(lambda f: "x", True, []) == []


def test_paths_in_working_copy_keeps_only_inside_and_dedupes(tmp_path: Path) -> None:
    root = tmp_path / "wc"
    (root / "geo").mkdir(parents=True)
    other = tmp_path / "elsewhere"
    values = [
        str(root / "geo" / "a.bgeo"),
        str(root / "geo" / ".." / "geo" / "a.bgeo"),
        str(other / "b.bgeo"),
        "",
        "  ",
        "ip",
        "md",
        str(tmp_path / "wc2" / "c.bgeo"),
    ]
    assert outputs.paths_in_working_copy(values, root) == [(root / "geo" / "a.bgeo").resolve()]


def test_paths_in_working_copy_resolves_relative_values_against_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert outputs.paths_in_working_copy(["out.bgeo"], tmp_path) == [
        (tmp_path / "out.bgeo").resolve()
    ]


class FakeParm:
    def __init__(self, value: object, token: str | None = None, timed: bool = False) -> None:
        self.value = value
        self.token = token if token is not None else str(value)
        self.timed = timed

    def eval(self) -> object:
        return self.value

    def evalAsString(self) -> str:
        return self.token

    def isTimeDependent(self) -> bool:
        return self.timed

    def evalAtFrame(self, frame: float) -> str:
        return str(self.value).replace("$F", f"{int(frame):04d}")


class FakeNode:
    def __init__(self, **parms: FakeParm) -> None:
        self.parms = parms

    def parm(self, name: str) -> FakeParm | None:
        return self.parms.get(name)

    def path(self) -> str:
        return "/obj/fake"


def test_node_outputs_walks_a_range_only_when_trange_asks_for_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hou, "frame", lambda: 7.0, raising=False)
    spec = OutputSpec(("sopoutput",))
    pattern = str(tmp_path / "r.$F.bgeo")

    def node(trange: int, token: str) -> FakeNode:
        return FakeNode(
            sopoutput=FakeParm(pattern, timed=True),
            trange=FakeParm(trange, token),
            f1=FakeParm(1.0),
            f2=FakeParm(3.0),
            f3=FakeParm(1.0),
        )

    names = lambda paths: [p.name for p in paths]
    assert names(outputs.node_outputs(node(1, "normal"), spec, tmp_path)) == [
        "r.0001.bgeo",
        "r.0002.bgeo",
        "r.0003.bgeo",
    ]
    assert names(outputs.node_outputs(node(0, "off"), spec, tmp_path)) == ["r.0007.bgeo"]
    assert names(outputs.node_outputs(node(3, "stage"), spec, tmp_path)) == ["r.0007.bgeo"]


def test_node_outputs_applies_the_gate_and_skips_missing_parms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hou, "frame", lambda: 1.0, raising=False)
    spec = OutputSpec(("file", "nope"), (("filemode", "write"),))
    target = FakeParm(str(tmp_path / "w.bgeo"))
    assert outputs.node_outputs(
        FakeNode(file=target, filemode=FakeParm(1, "read")), spec, tmp_path
    ) == []
    written = outputs.node_outputs(
        FakeNode(file=target, filemode=FakeParm(2, "write")), spec, tmp_path
    )
    assert [p.name for p in written] == ["w.bgeo"]
    assert outputs.node_outputs(FakeNode(file=target), spec, tmp_path) == []


def test_is_output_node_uses_defaults_and_remembered_overrides() -> None:
    node = SimpleNamespace(type=lambda: SimpleNamespace(nameWithCategory=lambda: "Sop/custom"))
    outputs.remember_overrides(None)
    assert not outputs.is_output_node(node)
    outputs.remember_overrides({"outputs": {"Sop/custom": {"parms": ["p"]}}})
    assert outputs.is_output_node(node)
    outputs.remember_overrides({"outputs": 5})
    assert not outputs.is_output_node(node)
    outputs.remember_overrides(None)


def capped_node(tmp_path: Path, last: float) -> FakeNode:
    return FakeNode(
        sopoutput=FakeParm(str(tmp_path / "r.bgeo")),
        trange=FakeParm(1, "normal"),
        f1=FakeParm(1.0),
        f2=FakeParm(last),
        f3=FakeParm(1.0),
    )


def test_frame_cap_logs_a_warning_and_fills_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hou, "frame", lambda: 1.0, raising=False)
    monkeypatch.setattr(outputs, "_MAX_FRAMES", 5)
    notes: list[str] = []
    with caplog.at_level("WARNING", logger="lore_dcc.houdini.outputs"):
        found = outputs.node_outputs(
            capped_node(tmp_path, 12), OutputSpec(("sopoutput",)), tmp_path, notes
        )
    assert [p.name for p in found] == ["r.bgeo"]
    assert len(notes) == 1
    assert "/obj/fake" in notes[0] and "12 frames" in notes[0] and "first 5" in notes[0]
    assert [r.getMessage() for r in caplog.records] == notes


def test_frame_cap_without_notes_still_logs_and_in_range_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hou, "frame", lambda: 1.0, raising=False)
    monkeypatch.setattr(outputs, "_MAX_FRAMES", 5)
    spec = OutputSpec(("sopoutput",))
    with caplog.at_level("WARNING", logger="lore_dcc.houdini.outputs"):
        outputs.node_outputs(capped_node(tmp_path, 12), spec, tmp_path)
        assert len(caplog.records) == 1
        notes: list[str] = []
        outputs.node_outputs(capped_node(tmp_path, 5), spec, tmp_path, notes)
    assert notes == []
    assert len(caplog.records) == 1


def test_outputs_for_node_passes_notes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hou, "frame", lambda: 1.0, raising=False)
    monkeypatch.setattr(outputs, "_MAX_FRAMES", 5)
    node = capped_node(tmp_path, 12)
    node.type = lambda: SimpleNamespace(nameWithCategory=lambda: "Sop/rop_geometry")
    session = SimpleNamespace(
        settings=SimpleNamespace(houdini={}), workspace=SimpleNamespace(root=tmp_path)
    )
    notes: list[str] = []
    assert len(outputs.outputs_for_node(node, session, notes)) == 1
    assert len(notes) == 1
