"""The File a Bug link fills the bug form's fields and never sends labels."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lore_dcc import __version__, bugs

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
