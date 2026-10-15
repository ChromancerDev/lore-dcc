"""The File a Bug link fills the bug form's fields and never sends labels."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lore_dcc import __version__, bugs
from lore_dcc.errors import LoreOutputError

FORM = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE" / bugs.TEMPLATE


def test_report_url_fills_the_form_fields() -> None:
    url = urlsplit(bugs.report_url("Houdini", "22.0.429"))
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == bugs.NEW_ISSUE
    assert query["template"] == bugs.TEMPLATE
    assert (query["dcc"], query["dcc-version"]) == ("Houdini", "22.0.429")
    assert query["plugin-version"] == __version__
    assert query["os"]
    assert "labels" not in query


def test_every_prefilled_field_exists_in_the_form() -> None:
    form = FORM.read_text(encoding="utf-8")
    query = parse_qs(urlsplit(bugs.report_url("Blender", "5.0")).query)
    for field in set(query) - {"template"}:
        assert f"id: {field}\n" in form


def test_the_dcc_field_keeps_the_heading_the_label_workflow_reads() -> None:
    form = FORM.read_text(encoding="utf-8")
    workflow = (FORM.parents[1] / "workflows" / "label-dcc.yml").read_text(encoding="utf-8")
    assert "id: dcc\n    attributes:\n      label: DCC\n" in form
    assert "/^### DCC$/" in workflow


SCREENSHOT_ERROR = r"""Unrecognised lore status line: '[Warn] Connection to lore://100.78.241.36:41337 failed'

[Warn] Connection to lore://100.78.241.36:41337 failed
Repository 01a0bd67f85871c1b216349e457ddc1e
On branch main revision 8 -> b5c30951c50135402d9fb46e6d836cabcc648ea029a592d081abea36757191aa
Signed in as ben@chromancer.xyz (user:74258166-474c-48da-be96-371255af2c38) from 100.78.241.36
File C:\Users\Ben\work\scene.blend and /home/ben/work/scene.hip"""


def _fresh(monkeypatch) -> None:
    monkeypatch.setattr(bugs, "_last_error", None)


def test_redact_masks_servers_ids_emails_ips_and_user_folders() -> None:
    text = bugs.redact(SCREENSHOT_ERROR)
    for private in ("100.78.241.36", "41337", "01a0bd67", "b5c30951", "ben@chromancer.xyz",
                    "74258166", "\\Ben\\", "/ben/"):  # fmt: skip
        assert private not in text
    assert "lore://<server>" in text
    assert "user:<id>" in text
    assert "On branch main revision 8 -> <id>" in text
    assert r"C:\Users\<user>\work\scene.blend" in text
    assert "/home/<user>/work/scene.hip" in text


def test_no_error_leaves_the_log_field_empty(monkeypatch) -> None:
    _fresh(monkeypatch)
    query = parse_qs(urlsplit(bugs.report_url("Houdini", "22.0")).query)
    assert "output" not in query


def test_last_error_fills_the_log_field_redacted(monkeypatch) -> None:
    _fresh(monkeypatch)
    error = LoreOutputError("Unrecognised lore status line", SCREENSHOT_ERROR)
    bugs.remember_error(error, "Lore")
    output = parse_qs(urlsplit(bugs.report_url("Blender", "5.2")).query)["output"][0]
    assert output.startswith("Lore\n\nUnrecognised lore status line")
    assert "lore://<server>" in output
    assert "100.78.241.36" not in output


def test_a_long_error_is_cut_to_fit_the_link(monkeypatch) -> None:
    _fresh(monkeypatch)
    bugs.remember_error(LoreOutputError("boom", "line of lore output\n" * 2000))
    url = bugs.report_url("Houdini", "22.0")
    assert len(url) <= bugs.MAX_URL
    output = parse_qs(urlsplit(url).query)["output"][0]
    assert output.startswith("boom") and output.endswith(bugs.TRIMMED)
