"""Shared message texts."""

from __future__ import annotations

from lore_dcc import summaries
from lore_dcc.session import BehindReport, SyncResult, WriteResult


def test_list_paths_caps_the_list() -> None:
    paths = [f"f{i}" for i in range(10)]
    lines = summaries.list_paths(paths).splitlines()
    assert lines[0] == "    f0"
    assert lines[-1] == "    and 2 more"
    assert len(lines) == summaries.LISTED + 1


def test_describe_write_and_sync_summary() -> None:
    assert summaries.describe_write(WriteResult(("a",), (), ("b",))) == (
        "Staged (new): 1\n    a\nReleased (unchanged): 1\n    b"
    )
    text = summaries.sync_summary(SyncResult(("a",), ("b",)), scene_skipped=True)
    assert text.startswith("Synced 1 file(s):\n    a")
    assert "Skipped:\n    b" in text
    assert text.endswith("The scene was not synced or reopened.")


def test_behind_texts() -> None:
    report = BehindReport(True, ("tex/a.png",))
    assert summaries.behind_offer_text(report) == (
        "This scene itself is behind the server.\n\n"
        "These references are behind the server:\n    tex/a.png"
    )
    assert "cannot be synced" in summaries.scene_behind_text(report, edited=True)
    assert "Sync and reopen" in summaries.scene_behind_text(report, edited=False)
    assert summaries.outside_text([("Image a", "D:/x/a.png")]).count("Image a: D:/x/a.png") == 1
