"""Parser tests against output captured from Lore 0.9.0+783."""

from __future__ import annotations

import pytest
from lore_dcc.backend import parsers
from lore_dcc.errors import LoreOutputError

SIG = "9" * 64

STATUS_UNTRACKED = f"""\
Repository 0123456789abcdef0123456789abcdef
On branch main revision 2 -> {SIG}
Remote revision 2 -> {SIG}
Local branch in sync with remote
Untracked files:
A source/test/
A source/test/cube/
A source/test/cube/Cube.hiplc
A source/test/cube/out.fbx
"""

STATUS_STAGED = f"""\
Repository 0123456789abcdef0123456789abcdef
On branch development revision 42 -> {SIG}
Remote revision 42 -> {SIG}
Local branch in sync with remote
Changes staged for commit:
A newasset/ \n\
A newasset/images/ \n\
A newasset/images/a.exr \n\
A newasset/scan.json \n\
"""

STATUS_SERVER_DOWN = f"""\
Repository 0123456789abcdef0123456789abcdef
On branch development revision 42 -> {SIG}
"""

# Not yet captured on 0.9: section shape carried from Lore 0.8.6 parsers.
STATUS_TRACKED = f"""\
On branch main revision 7 -> {SIG}
Remote revision 7 -> {SIG}
Local branch in sync with remote
Changes staged for commit:
  Tracked changes:
M scenes/shot.hip
Changes not staged for commit:
  Tracked changes:
D caches/old.bgeo.sc
"""


def test_version() -> None:
    assert parsers.parse_version("lore 0.9.0+783\n") == (0, 9, 0)
    assert parsers.parse_version("lore 1.2.3") == (1, 2, 3)


def test_version_rejects_unknown() -> None:
    with pytest.raises(LoreOutputError):
        parsers.parse_version("lore version 0.9")


def test_status_untracked() -> None:
    status = parsers.parse_status(STATUS_UNTRACKED)
    assert (status.branch, status.revision, status.remote_revision) == ("main", 2, 2)
    assert status.remote_state is parsers.RemoteState.IN_SYNC
    assert status.staged == ()
    assert [c.path for c in status.untracked] == [
        "source/test",
        "source/test/cube",
        "source/test/cube/Cube.hiplc",
        "source/test/cube/out.fbx",
    ]
    assert [c.is_dir for c in status.untracked] == [True, True, False, False]


def test_status_staged_lines_have_trailing_spaces() -> None:
    status = parsers.parse_status(STATUS_STAGED)
    assert [c.path for c in status.staged if not c.is_dir] == [
        "newasset/images/a.exr",
        "newasset/scan.json",
    ]


def test_status_tracked_sections() -> None:
    status = parsers.parse_status(STATUS_TRACKED)
    assert status.staged == (parsers.Change("M", "scenes/shot.hip", False),)
    assert status.unstaged == (parsers.Change("D", "caches/old.bgeo.sc", False),)


def test_status_server_down_has_no_remote() -> None:
    status = parsers.parse_status(STATUS_SERVER_DOWN)
    assert status.remote_revision is None
    assert status.remote_state is parsers.RemoteState.UNKNOWN


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        ("Local branch is behind remote", parsers.RemoteState.BEHIND),
        ("Local branch has diverged, synchronize to merge", parsers.RemoteState.DIVERGED),
        ("Local branch is something new", parsers.RemoteState.UNKNOWN),
    ],
)
def test_status_remote_state(note: str, expected: parsers.RemoteState) -> None:
    text = f"""\
Repository 0123456789abcdef0123456789abcdef
On branch main revision 2 -> {SIG}
Remote revision 3 -> {"8" * 64}
{note}
"""
    status = parsers.parse_status(text)
    assert (status.revision, status.remote_revision) == (2, 3)
    assert status.remote_state is expected


@pytest.mark.parametrize(
    "text",
    [
        STATUS_UNTRACKED + "R moved/file.hip\n",
        STATUS_UNTRACKED + "Something new:\n",
        f"On branch main revision 1 -> {SIG}\nM before/any/section.hip\n",
    ],
    ids=["unknown-code", "unknown-header", "change-outside-section"],
)
def test_status_fails_closed(text: str) -> None:
    with pytest.raises(LoreOutputError) as info:
        parsers.parse_status(text)
    assert info.value.raw == text


def test_status_needs_branch_line() -> None:
    with pytest.raises(LoreOutputError):
        parsers.parse_status("Local branch in sync with remote\n")


def test_auth_info() -> None:
    info = parsers.parse_auth_info("ID: user:0000-1111\nName: artist@example.com\n")
    assert info == parsers.AuthInfo("user:0000-1111", "artist@example.com")


def test_auth_info_fails_closed() -> None:
    with pytest.raises(LoreOutputError):
        parsers.parse_auth_info("Not logged in\n")


def test_lock_query_empty() -> None:
    assert parsers.parse_lock_query("Locks found:\n") == ()


def test_lock_query_records() -> None:
    text = (
        "Locks found:\n"
        "README.md by artist@example.com on branch e726318bbc3fd75ac8733a7e030cc35b\n"
        "scenes/shot v2.hip by artist@example.com on branch e726318bbc3fd75ac8733a7e030cc35b\n"
    )
    first, second = parsers.parse_lock_query(text)
    assert (first.path, first.owner, first.branch) == (
        "README.md",
        "artist@example.com",
        "e726318bbc3fd75ac8733a7e030cc35b",
    )
    assert second.path == "scenes/shot v2.hip"


@pytest.mark.parametrize("text", ["", "No locks\n", "Locks found:\ngarbage line\n"])
def test_lock_query_fails_closed(text: str) -> None:
    with pytest.raises(LoreOutputError):
        parsers.parse_lock_query(text)


def test_lock_acquire_new_and_already_owned() -> None:
    assert parsers.parse_lock_acquire("Lock acquired on files:\nsource/test/cube/Cube.hiplc\n") == {
        "source/test/cube/Cube.hiplc"
    }
    assert parsers.parse_lock_acquire("Lock already owned on files:\nlore-dcc-spike/f001.txt\n") == {
        "lore-dcc-spike/f001.txt"
    }


@pytest.mark.parametrize(
    "text",
    [
        "Lock owned by someone else on files:\nREADME.md\n",
        "README.md\n",
        "Lock acquired on files:\nREADME.md\nSomething else:\nother.hip\n",
    ],
    ids=["unknown-header", "no-header", "second-unknown-header"],
)
def test_lock_acquire_fails_closed(text: str) -> None:
    with pytest.raises(LoreOutputError):
        parsers.parse_lock_acquire(text)
