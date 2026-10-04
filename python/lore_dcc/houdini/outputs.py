"""Find the files a scene writes: the output node table, frame lists and the scene scan.

The table, frame and path logic is pure Python. `hou` is imported only by the
functions that read a node.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lore_dcc.errors import SettingsError

if TYPE_CHECKING:
    from lore_dcc.session import Session

logger = logging.getLogger(__name__)

_VERSION = re.compile(r"^\d+(\.\d+)*$")
# Mantra and OpenGL pictures named "ip" or "md" go to the viewer, not to disk.
_NOT_FILES = frozenset({"ip", "md"})
_MAX_FRAMES = 100_000


@dataclass(frozen=True)
class OutputSpec:
    """The parms of one node type that name files the node writes.

    Attributes:
        parms: Parm names holding output file paths. Missing parms are skipped.
        when: Parm name to required value. All must match or the node writes
            nothing. An int matches the parm's value, a string its menu token.
    """

    parms: tuple[str, ...]
    when: tuple[tuple[str, int | str], ...] = ()


def _spec(*parms: str, when: dict[str, int | str] | None = None) -> OutputSpec:
    return OutputSpec(parms, tuple((when or {}).items()))


DEFAULT_OUTPUTS: dict[str, OutputSpec] = {
    "Sop/file": _spec("file", when={"filemode": "write"}),
    "Sop/filecache": _spec("sopoutput"),
    "Sop/rop_geometry": _spec("sopoutput"),
    "Sop/rop_alembic": _spec("filename"),
    "Sop/rop_fbx": _spec("sopoutput"),
    "Sop/rop_gltf": _spec("outputfile"),
    "Sop/kinefx::rop_fbxanimoutput": _spec("outputfilepath"),
    "Sop/kinefx::rop_fbxcharacteroutput": _spec("outputfilepath"),
    "Sop/kinefx::rop_gltfcharacteroutput": _spec("filepath"),
    "Sop/usdexport": _spec("lopoutput"),
    "Driver/geometry": _spec("sopoutput"),
    "Driver/alembic": _spec("filename"),
    "Driver/filmboxfbx": _spec("sopoutput"),
    "Driver/gltf": _spec("outputfile"),
    "Driver/kinefx::filmboxfbxanimation": _spec("outputfilepath"),
    "Driver/kinefx::filmboxfbxcharacter": _spec("outputfilepath"),
    "Driver/kinefx::gltfcharacter": _spec("filepath"),
    "Driver/usd": _spec("lopoutput"),
    "Driver/karma": _spec("picture"),
    "Driver/ifd": _spec("vm_picture"),
    "Driver/image": _spec("copoutput"),
    "Lop/usd_rop": _spec("lopoutput"),
    "Lop/karma": _spec("picture"),
    "Lop/filecache": _spec("lopoutput"),
    "Cop/rop_image": _spec("copoutput"),
}


def table_keys(name_with_category: str) -> tuple[str, ...]:
    """Return the table keys that match a node type, most specific first.

    `Sop/filecache::2.0` gives `Sop/filecache::2.0`, `Sop/filecache`,
    `filecache::2.0` and `filecache`. A trailing version is dropped.
    """
    category, _, name = name_with_category.partition("/")
    head, sep, tail = name.rpartition("::")
    base = head if sep and _VERSION.match(tail) else name
    keys = (f"{category}/{name}", f"{category}/{base}", name, base)
    return tuple(dict.fromkeys(keys))


def parse_overrides(raw: object) -> dict[str, OutputSpec]:
    """Validate the `outputs` object of the `houdini` section of lore-dcc.json.

    Shape: `{"Sop/filecache": {"parms": ["sopoutput"], "when": {"filemode": 2}}}`.
    A key is a node type name, optionally prefixed by its category and a slash.

    Raises:
        SettingsError: The shape is wrong.
    """
    where = "lore-dcc.json houdini.outputs"
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SettingsError(f"{where} must be an object")
    table: dict[str, OutputSpec] = {}
    for key, value in raw.items():
        label = f'{where}["{key}"]'
        if not isinstance(key, str) or not key.strip():
            raise SettingsError(f"{where} keys must be node type names")
        if not isinstance(value, dict) or set(value) - {"parms", "when"}:
            raise SettingsError(f"{label} must be an object with parms and optional when")
        parms = value.get("parms")
        if (
            not isinstance(parms, list)
            or not parms
            or not all(isinstance(p, str) and p for p in parms)
        ):
            raise SettingsError(f"{label}.parms must be a non-empty list of parm names")
        when = value.get("when", {})
        if not isinstance(when, dict) or not all(
            isinstance(k, str) and isinstance(v, int | str) and not isinstance(v, bool)
            for k, v in when.items()
        ):
            raise SettingsError(f"{label}.when must map parm names to an integer or a string")
        table[key.strip()] = OutputSpec(tuple(parms), tuple(when.items()))
    return table


@dataclass(frozen=True)
class OutputTable:
    """Default output nodes plus the repo's overrides.

    Attributes:
        overrides: Entries from lore-dcc.json. They win over the defaults for
            any node type they match and replace that default entry whole.
    """

    overrides: Mapping[str, OutputSpec] = field(default_factory=dict)

    def find(self, name_with_category: str) -> OutputSpec | None:
        """Return the spec for a node type such as `Sop/filecache::2.0`, or None."""
        keys = table_keys(name_with_category)
        for table in (self.overrides, DEFAULT_OUTPUTS):
            for key in keys:
                if key in table:
                    return table[key]
        return None


def frame_list(start: float, end: float, step: float) -> list[float]:
    """Return the frames a range renders: start, start+step, ... up to end.

    A step of zero or less counts as 1. An empty range gives an empty list. A
    range of more than 100,000 frames is cut to its first 100,000.
    """
    if step <= 0:
        step = 1.0
    count = min(frame_count(start, end, step), _MAX_FRAMES)
    return [start + i * step for i in range(count)]


def frame_count(start: float, end: float, step: float) -> int:
    """Return how many frames a range holds before the cap that frame_list applies."""
    if step <= 0:
        step = 1.0
    if end < start:
        return 0
    return int((end - start) / step + 1e-6) + 1


def when_matches(
    when: Iterable[tuple[str, int | str]], read: Callable[[str, bool], object]
) -> bool:
    """Return True when every `when` condition holds.

    Args:
        when: Pairs of parm name and required value.
        read: Returns a parm's value, as its menu token when the second
            argument is True, or None when the node has no such parm.
    """
    return all(read(name, isinstance(want, str)) == want for name, want in when)


def evaluate_paths(
    eval_at: Callable[[float], str], time_dependent: bool, frames: Sequence[float]
) -> list[str]:
    """Evaluate one parm over `frames` and return its distinct values in order.

    A parm that does not change with time is evaluated once, at the first frame.
    """
    if not frames:
        return []
    if not time_dependent:
        return [eval_at(frames[0])]
    return list(dict.fromkeys(eval_at(frame) for frame in frames))


def paths_in_working_copy(values: Iterable[str], root: Path) -> list[Path]:
    """Return the distinct absolute paths among `values` that lie inside `root`.

    Blank values and the viewer names `ip` and `md` are dropped. Relative
    values resolve against the process working folder, as Houdini writes them.
    """
    root = Path(root).resolve()
    found: dict[Path, None] = {}
    for value in values:
        value = value.strip()
        if not value or value in _NOT_FILES:
            continue
        path = Path(os.path.abspath(value)).resolve()
        if path.is_relative_to(root):
            found[path] = None
    return list(found)


_remembered: dict[str, OutputSpec] = {}


def remember_overrides(houdini_settings: Mapping[str, Any] | None) -> None:
    """Keep a working copy's overrides so node menus can show for its output nodes."""
    global _remembered
    try:
        _remembered = parse_overrides((houdini_settings or {}).get("outputs"))
    except SettingsError as exc:
        logger.warning("%s", exc)
        _remembered = {}


def _table_for(session: Session) -> OutputTable:
    houdini = getattr(session.settings, "houdini", None) or {}
    return OutputTable(parse_overrides(houdini.get("outputs")))


def is_output_node(node: Any) -> bool:
    """Return True when the node's type is in the output table (menus use this)."""
    return OutputTable(_remembered).find(node.type().nameWithCategory()) is not None


def _node_frames(node: Any, current: float, notes: list[str] | None = None) -> list[float]:
    trange = node.parm("trange")
    if trange is None or trange.eval() == 0 or trange.evalAsString() == "stage":
        return [current]
    f1, f2, f3 = node.parm("f1"), node.parm("f2"), node.parm("f3")
    if f1 is None or f2 is None:
        return [current]
    start, end, step = f1.eval(), f2.eval(), f3.eval() if f3 is not None else 1.0
    total = frame_count(start, end, step)
    if total > _MAX_FRAMES:
        note = (
            f"{node.path()} renders {total:,} frames; only the first {_MAX_FRAMES:,} "
            "are covered, so later frame files are not locked or collected."
        )
        logger.warning("%s", note)
        if notes is not None:
            notes.append(note)
    return frame_list(start, end, step)


def _read_parm(node: Any, name: str, as_token: bool) -> object:
    parm = node.parm(name)
    if parm is None:
        return None
    return parm.evalAsString() if as_token else parm.eval()


def node_outputs(
    node: Any, spec: OutputSpec, root: Path, notes: list[str] | None = None
) -> list[Path]:
    """Return the files `node` writes inside `root`.

    A node that renders a range is evaluated at every frame of its own range,
    any other node at the current frame. A USD or Karma ROP set to take its
    range from the stage counts as a single frame. A range over the frame cap
    is cut short with a logged warning, and the same text is appended to
    `notes` when given.
    """
    import hou

    if not when_matches(spec.when, lambda name, token: _read_parm(node, name, token)):
        return []
    frames = _node_frames(node, hou.frame(), notes)
    values: list[str] = []
    for name in spec.parms:
        parm = node.parm(name)
        if parm is None:
            continue
        try:
            values += evaluate_paths(parm.evalAtFrame, parm.isTimeDependent(), frames)
        except hou.Error:
            logger.exception("Could not evaluate %s on %s", name, node.path())
    return paths_in_working_copy(values, root)


def outputs_for_node(
    node: Any, session: Session, notes: list[str] | None = None
) -> list[Path]:
    """Return the files one node writes, or an empty list when it is not an output node.

    Warnings, such as a frame range cut by the cap, are appended to `notes`.

    Raises:
        SettingsError: The repo's output table is malformed.
    """
    spec = _table_for(session).find(node.type().nameWithCategory())
    return node_outputs(node, spec, session.workspace.root, notes) if spec else []


def scan_scene(session: Session, notes: list[str] | None = None) -> list[Path]:
    """Return every file the whole scene writes inside the working copy.

    Warnings, such as a frame range cut by the cap, are appended to `notes`.

    Raises:
        SettingsError: The repo's output table is malformed.
    """
    import hou

    table = _table_for(session)
    found: dict[Path, None] = {}
    for node in hou.node("/").allSubChildren():
        spec = table.find(node.type().nameWithCategory())
        if spec:
            found.update(dict.fromkeys(node_outputs(node, spec, session.workspace.root, notes)))
    return list(found)
