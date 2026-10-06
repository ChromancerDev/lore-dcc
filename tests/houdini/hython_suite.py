"""Integration checks that run inside hython. Not collected by pytest.

The wrapper in test_houdini_hython.py starts this script with
HOUDINI_PACKAGE_DIR pointing at the repo's `houdini/` folder and
HOUDINI_USER_PREF_DIR pointing at an empty folder. The Lore session is a fake.
Exit code 0 means every check passed.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import hou

failures: list[str] = []


def check(condition: object, label: str) -> None:
    print(("ok    " if condition else "FAIL  ") + label)
    if not condition:
        failures.append(label)


def set_range(node: hou.Node, start: float, end: float, step: float) -> None:
    for name, value in (("f1", start), ("f2", end), ("f3", step)):
        parm = node.parm(name)
        parm.deleteAllKeyframes()
        parm.set(value)


def norm(path: object) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())

    def saw(self, text: str) -> bool:
        return any(text in line for line in self.lines)


capture = Capture()
logging.getLogger("lore_dcc").addHandler(capture)
logging.getLogger("lore_dcc").setLevel(logging.INFO)

print("Houdini", hou.applicationVersionString(), "UI available:", hou.isUIAvailable())

# 1. Package loaded, ready.py ran, hooks registered once.
python_dir = os.environ.get("LORE_DCC_PYTHON") or hou.getenv("LORE_DCC_PYTHON")
check(python_dir, "package set LORE_DCC_PYTHON")
check(python_dir and (Path(python_dir) / "lore_dcc" / "houdini").is_dir(), "LORE_DCC_PYTHON holds lore_dcc/houdini")
check("lore_dcc.houdini.hooks" in sys.modules, "ready.py imported lore_dcc.houdini.hooks")

from lore_dcc import prefs
from lore_dcc import session as session_module
from lore_dcc.errors import NotAWorkingCopy
from lore_dcc.houdini import actions, api, hooks, host, outputs, references
from lore_dcc.locks import LockState
from lore_dcc.session import FileState, WriteResult


def tagged_hip() -> int:
    return sum(1 for cb in hou.hipFile.eventCallbacks() if getattr(cb, "lore_dcc_hook", False))


def tagged_hda() -> int:
    return sum(1 for _t, cb in hou.hda.eventCallbacks() if getattr(cb, "lore_dcc_hook", False))


check(tagged_hip() == 1, "one hip callback registered by ready.py")
check(tagged_hda() == 1, "one asset callback registered by ready.py")
hooks.install()
hooks.install()
check(tagged_hip() == 1 and tagged_hda() == 1, "install() is idempotent")
scratch = Path(os.environ["LORE_DCC_TEST_ROOT"]).resolve()
prefs_inside_scratch = Path(host.prefs_folder()).resolve().is_relative_to(scratch)
check(prefs_inside_scratch, "prefs folder follows HOUDINI_USER_PREF_DIR")
if not prefs_inside_scratch:
    sys.exit("refusing to write prefs into a real Houdini preferences folder")
check(not host.has_ui(), "no UI in hython")
try:
    import PySide6  # noqa: F401

    print("info  PySide6 imports in hython")
except ImportError:
    print("info  PySide6 does not import in hython")

# 2. Fake Lore session.
work = Path(tempfile.mkdtemp(prefix="lore_dcc_work_")).resolve()
outside = Path(tempfile.mkdtemp(prefix="lore_dcc_outside_")).resolve()


class FakeSession:
    def __init__(self) -> None:
        self.workspace = SimpleNamespace(root=work, relative=lambda p: Path(p).relative_to(work).as_posix())
        self.settings = SimpleNamespace(houdini={})
        self.calls: list[tuple[str, list[str]]] = []
        self.state = {"exists": True, "tracked": False, "lock": LockState.NOT_LOCKED, "owner": None}
        self.fail_states = False

    def _record(self, name: str, paths: object) -> None:
        self.calls.append((name, [norm(p) for p in paths]))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def paths(self, name: str) -> list[str]:
        return [p for n, ps in self.calls if n == name for p in ps]

    def file_states(self, paths):
        self._record("file_states", paths)
        if self.fail_states:
            raise RuntimeError("boom")
        return [FileState(path=str(p), lockable=True, **self.state) for p in paths]

    def mark_saved(self, paths):
        self._record("mark_saved", paths)

    def lock(self, paths):
        self._record("lock", paths)

    def stage(self, paths):
        self._record("stage", paths)

    def is_staged(self, path):
        self._record("is_staged", [path])
        return False

    def invalidate(self):
        self.calls.append(("invalidate", []))

    def prepare_write(self, paths):
        self._record("prepare_write", paths)
        return tuple(str(p) for p in paths)

    def finish_write(self, paths):
        self._record("finish_write", paths)
        return WriteResult(staged=(), changed=tuple(str(p) for p in paths), released=())

    def forget(self) -> None:
        self.calls.clear()


fake = FakeSession()


def fake_session_for(path):
    path = Path(path).resolve()
    if not path.is_relative_to(work):
        raise NotAWorkingCopy(f"Not a Lore working copy: {path}")
    return fake


session_module.session_for = fake_session_for

# 3. Hip hooks. Without a UI a prompt is logged and nothing else happens.
hip = work / "scene.hip"
hou.hipFile.save(file_name=str(hip))
check(norm(hou.hipFile.path()) == norm(hip), "hipFile.path() is the Save As target")
check(
    fake.names() == ["file_states", "mark_saved", "file_states", "is_staged"],
    "save of a new file: state, mark_saved, state, staged check",
)
check(fake.paths("mark_saved") == [norm(hip)], "mark_saved got the saved file")
check(capture.saw("Lore - Stage New File?"), "untracked file: stage prompt logged, not shown")
check("stage" not in fake.names(), "no stage without a UI")

fake.forget()
fake.state = {"exists": True, "tracked": True, "lock": LockState.NOT_LOCKED, "owner": None}
hou.hipFile.save()
check(capture.saw("Lore - Lock File?"), "tracked unlocked file: lock prompt logged")
check("lock" not in fake.names(), "prompts change nothing without a UI")
check("mark_saved" in fake.names(), "mark_saved runs on every save")

fake.forget()
fake.state = {"exists": True, "tracked": True, "lock": LockState.LOCKED_BY_ME, "owner": "me"}
hou.hipFile.save()
check(fake.names() == ["file_states", "mark_saved", "file_states"], "locked by me: no prompt, no change")

fake.forget()
fake.state = {"exists": True, "tracked": True, "lock": LockState.LOCKED_BY_OTHER, "owner": "Ana"}
hou.hipFile.save()
check(capture.saw("locked by Ana"), "locked by other: logged, and the save goes ahead")
check("lock" not in fake.names(), "locked by other: nothing changed without a UI")

fake.forget()
fake.state = {"exists": True, "tracked": True, "lock": LockState.UNKNOWN, "owner": None}
hou.hipFile.save()
check(capture.saw("unknown"), "unknown lock state is logged")
check("lock" not in fake.names(), "unknown state changes nothing")

fake.forget()
fake.fail_states = True
hou.hipFile.save()
check(hip.is_file(), "a failing Lore call never stops the save")
fake.fail_states = False

fake.forget()
hou.hipFile.load(str(hip), suppress_save_prompt=True)
check(fake.names() == ["invalidate"], "AfterLoad drops the caches")

fake.forget()
stray = outside / "stray.hip"
hou.hipFile.save(file_name=str(stray))
check(fake.calls == [], "outside a working copy: hooks do nothing")
hou.hipFile.load(str(hip), suppress_save_prompt=True)

fake.forget()
prefs.save_prefs(host.prefs_folder(), prefs.Prefs(enabled=False))
hou.hipFile.save()
check(fake.calls == [], "disabled: hooks do nothing")
prefs.save_prefs(host.prefs_folder(), prefs.Prefs())

# 4. Asset hooks.
fake.forget()
fake.state = {"exists": True, "tracked": False, "lock": LockState.NOT_LOCKED, "owner": None}
geo = hou.node("/obj").createNode("geo")
subnet = geo.createNode("subnet")
hda_file = work / "probe.hda"
asset = subnet.createDigitalAsset(name="lore_probe", hda_file_name=str(hda_file), description="Probe")
check("file_states" in fake.names() and "mark_saved" in fake.names(), "asset save runs both hooks")
check(set(fake.paths("mark_saved")) == {norm(hda_file)}, "asset hooks use the library file")
check(actions.node_has_library(asset), "node menu sees an asset instance")
check(not actions.node_has_library(geo), "node menu hides for built-in nodes")

# 5. Output scan.
fake.forget()
hou.hipFile.save()
sops = hou.node("/obj/geo1")
cache = sops.createNode("filecache")
cache.parm("filemethod").set(1)
cache.parm("file").set("$HIP/geo/fc.$F4.bgeo.sc")
cache.parm("trange").set(1)
set_range(cache, 1, 3, 1)
single = sops.createNode("filecache")
single.parm("filemethod").set(1)
single.parm("file").set("$HIP/geo/single.$F4.bgeo.sc")
single.parm("trange").set(0)
rop = sops.createNode("rop_geometry")
rop.parm("sopoutput").set("$HIP/rop/r.$F4.bgeo.sc")
rop.parm("trange").set(1)
set_range(rop, 5, 9, 2)
abc = sops.createNode("rop_alembic")
abc.parm("filename").set("$HIP/abc/a.abc")
abc.parm("trange").set(1)
set_range(abc, 1, 50, 1)
elsewhere = sops.createNode("rop_geometry")
elsewhere.parm("sopoutput").set(str(outside / "out.$F4.bgeo.sc"))
reader = sops.createNode("file")
reader.parm("file").set("$HIP/read.bgeo")
writer = sops.createNode("file")
writer.parm("filemode").set(2)
writer.parm("file").set("$HIP/write.bgeo")
raw = sops.createNode("rop_geometryraw")
raw.parm("sopoutput").set("$HIP/raw/x.raw")
hou.setFrame(4)

expected = {
    work / "geo" / f"fc.{n:04d}.bgeo.sc" for n in (1, 2, 3)
} | {
    work / "geo" / "single.0004.bgeo.sc",
    *(work / "rop" / f"r.{n:04d}.bgeo.sc" for n in (5, 7, 9)),
    work / "abc" / "a.abc",
    work / "write.bgeo",
}
found = outputs.scan_scene(fake)
check(set(found) == expected, "scan finds exactly the expected frame paths")
if set(found) != expected:
    print("  extra:", sorted(map(str, set(found) - expected)))
    print("  missing:", sorted(map(str, expected - set(found))))
check(len(found) == len(set(found)), "scan has no duplicates")
check(work / "geo" / "single.0004.bgeo.sc" in found, "single-frame cache uses the current frame")
check(not any(outside in p.parents for p in found), "paths outside the working copy are dropped")
check(outputs.is_output_node(rop) and not outputs.is_output_node(geo), "menu test: table membership")

constructed = sops.createNode("filecache")
constructed.parm("trange").set(1)
set_range(constructed, 1, 2, 1)
paths = outputs.node_outputs(constructed, outputs.DEFAULT_OUTPUTS["Sop/filecache"], work)
check(len(paths) == 2 and paths[0] != paths[1], "constructed File Cache path follows the frame")

fake.settings = SimpleNamespace(houdini={"outputs": {"Sop/rop_geometryraw": {"parms": ["sopoutput"]}}})
check(work / "raw" / "x.raw" in outputs.scan_scene(fake), "lore-dcc.json override adds a node type")
fake.settings = SimpleNamespace(houdini={})

# 6. Render helpers.
fake.forget()
locked = api.pre_render(rop)
check(
    fake.paths("prepare_write") == [norm(work / "rop" / f"r.{n:04d}.bgeo.sc") for n in (5, 7, 9)],
    "pre_render locks the node's frame paths",
)
check(len(locked) == 3, "pre_render returns what the session locked")
result = api.post_render(rop)
check(fake.paths("finish_write") == fake.paths("prepare_write"), "post_render collects the same paths")
check(isinstance(result, WriteResult), "post_render returns the WriteResult")
check(api.pre_render(geo) == (), "pre_render on a non-output node does nothing")
fake.forget()
check(api.pre_render(elsewhere) == () and fake.calls == [], "outputs outside the working copy are ignored")

fake.forget()
(work / ".lore").mkdir(exist_ok=True)
exported = [str(outside / "c.fbx"), work / "export" / "a.fbx", "$HIP/tex/b.exr"]
check(len(api.lock(exported)) == 2, "lock returns what the session locked")
wanted = [norm(work / "export" / "a.fbx"), norm(work / "tex" / "b.exr")]
check(
    fake.paths("prepare_write") == wanted,
    "lock expands $HIP and drops paths outside the working copy, even listed first",
)
check(isinstance(api.stage(work / "export" / "a.fbx"), WriteResult), "stage takes one path")
check(fake.paths("finish_write") == wanted[:1], "stage reports the written file")
fake.forget()
check(api.stage([str(outside / "c.fbx")]) is None and fake.calls == [], "stage outside a working copy does nothing")
check(api.lock([]) == () and fake.calls == [], "lock with no paths does nothing")


def failing_prepare(paths):
    raise RuntimeError("lock failed")


fake.prepare_write = failing_prepare
try:
    api.pre_render(rop)
except RuntimeError:
    check(True, "pre_render raises without a UI so the render stops")
else:
    check(False, "pre_render raises without a UI so the render stops")
try:
    api.lock(work / "export" / "a.fbx")
except RuntimeError:
    check(True, "a refused lock raises so the script stops")
else:
    check(False, "a refused lock raises so the script stops")

# 7. Reference scan and reload.
sops = hou.node("/obj/geo1")
inner = sops.createNode("file", "ref_inside")
inner.parm("file").set("$HIP/refs/in.bgeo")
foreign = outside / "ext.bgeo"
foreign.write_bytes(b"x")
stranger = sops.createNode("file", "ref_outside")
stranger.parm("file").set(str(foreign))
gone = sops.createNode("file", "ref_outside_missing")
gone.parm("file").set(str(outside / "gone.bgeo"))
scene = references.scan(work)
check(work / "refs" / "in.bgeo" in scene.inside, "scan: a File SOP inside the working copy is a reference")
check(
    (stranger.parm("file").path(), str(foreign)) in scene.outside,
    "scan: an existing file outside the working copy is reported with its parm path",
)
check(
    not any("gone.bgeo" in file for _where, file in scene.outside),
    "scan: a missing file outside the working copy is dropped",
)
check(work / "probe.hda" in scene.inside, "scan: the HDA library file the scene uses is a reference")

changing = work / "changing.bgeo"


def write_points(count: int) -> None:
    geometry = hou.Geometry()
    for _ in range(count):
        geometry.createPoint()
    geometry.saveToFile(str(changing))


write_points(1)
reader = sops.createNode("file", "ref_reload")
reader.parm("file").set(str(changing))
check(len(reader.geometry().points()) == 1, "reload: File SOP reads the first file")
time.sleep(1.5)
write_points(3)
references.reload([changing, work / "probe.hda"])
check(len(reader.geometry().points()) == 3, "reload: File SOP reads the rewritten file")

shutil.rmtree(work, ignore_errors=True)
shutil.rmtree(outside, ignore_errors=True)
print(f"{len(failures)} failure(s)")
sys.exit(1 if failures else 0)
