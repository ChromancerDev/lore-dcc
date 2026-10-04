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


def test_status_check_dirty_with_no_changes() -> None:
    text = f"""\
Repository 0123456789abcdef0123456789abcdef
On branch main revision 4 -> {SIG}
Remote revision 4 -> {SIG}
Local branch in sync with remote
No tracked changes
"""
    status = parsers.parse_status(text)
    assert (status.staged, status.unstaged, status.untracked) == ((), (), ())


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


FILE_INFO = """\
Path:    source/test/cube/Cube.hiplc
Type:    file
Size:    0
Mode:    000
Context: 00000000000000000000000000000000
Hash:    0000000000000000000000000000000000000000000000000000000000000000
Status:  Added
Path:    README.md
Type:    file
Size:    1080
Mode:    000
Context: 01a01c8a97967810943481d293b74f25
Hash:    b37fad19aeb151fccdc56c5efbaa05b5b69b3dd74124ece75b9a530f2f646e47
Status:  -
Path:    source
Type:    dir
Size:    0
Mode:    000
Context: 01a01c8a97967810943481d293b74f25
Hash:    b37fad19aeb151fccdc56c5efbaa05b5b69b3dd74124ece75b9a530f2f646e47
Status:  Modified
"""


def test_file_info_blocks_keyed_by_path() -> None:
    infos = parsers.parse_file_info(FILE_INFO)
    assert list(infos) == ["source/test/cube/Cube.hiplc", "README.md", "source"]
    new = infos["source/test/cube/Cube.hiplc"]
    assert (new.status, new.tracked) == ("Added", False)
    assert (infos["README.md"].status, infos["README.md"].tracked) == ("-", True)
    assert infos["source"].tracked


@pytest.mark.parametrize("status", ["-", "Modified", "Deleted"])
def test_file_info_tracked_statuses(status: str) -> None:
    block = FILE_INFO.split("Path:    README.md")[1].split("Path:    source")[0]
    text = "Path:    README.md" + block.replace("Status:  -", f"Status:  {status}")
    assert parsers.parse_file_info(text)["README.md"].tracked


def test_file_info_empty_output_is_no_blocks() -> None:
    assert parsers.parse_file_info("") == {}
    assert parsers.parse_file_info("\n\n") == {}


@pytest.mark.parametrize(
    "text",
    [
        FILE_INFO.replace("Status:  Added", "Status:  Ignored"),
        FILE_INFO.replace("Mode:    000", "Perms:   000", 1),
        FILE_INFO.replace("Size:    0\n", "Size:    big\n", 1),
        FILE_INFO.replace("Size:    1080\n", "", 1),
        "Type:    file\n" + FILE_INFO,
        "Path:    a\nPath:    b\n",
        FILE_INFO.replace("Status:  Added\n", "Status:  Added\nStatus:  Added\n", 1),
        "not a block\n",
    ],
)
def test_file_info_fails_closed(text: str) -> None:
    with pytest.raises(LoreOutputError):
        parsers.parse_file_info(text)


BRANCH_INFO = """\
Branch main
  ID: e726318bbc3fd75ac8733a7e030cc35b
  Latest: af62673685f70eb75c7018884116c9c7efa09cf395d929864698b77f27ee2843
  Remote Latest: af62673685f70eb75c7018884116c9c7efa09cf395d929864698b77f27ee2843
  Creator: artist@example.com
  Created: Thu, 20 Aug 2026 00:18:59 +0000
"""


def test_branch_info() -> None:
    info = parsers.parse_branch_info(BRANCH_INFO)
    assert (info.name, info.id) == ("main", "e726318bbc3fd75ac8733a7e030cc35b")


def test_branch_info_optional_keys_may_be_absent() -> None:
    info = parsers.parse_branch_info("Branch feature one\n  ID: ab12\n")
    assert (info.name, info.id) == ("feature one", "ab12")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "No branch\n",
        "  ID: ab12\n",
        "Branch\n  ID: ab12\n",
        "Branch main\n",
        "Branch main\n  Latest: ab\n",
        "Branch main\n  ID: ab12\n  Owner: me\n",
        "Branch main\n  ID: ab12\n  ID: cd34\n",
        "Branch main\n  ID: ab12\n  garbage\n",
        "Branch main\n  ID:\n",
    ],
)
def test_branch_info_fails_closed(text: str) -> None:
    with pytest.raises(LoreOutputError):
        parsers.parse_branch_info(text)


COMMIT_OUT = f"""\
Repository 01a01c890d5c78629798bab981f4d893
Revision  : 3
Signature : {SIG}
Commit succeeded
"""

PUSH_OUT = f"""\
Local branch is 1 revision(s) ahead of remote, pushing all revisions
Repository 01a01c890d5c78629798bab981f4d893
Pushing 1 fragment(s)
Pushed 1 fragment(s), 170.00 bytes
Pushing 775e00ea... to branch main
Pushed revision 3 -> {SIG} to branch main
"""


def test_committed_revision_reads_the_revision_line() -> None:
    assert parsers.committed_revision(COMMIT_OUT) == 3


@pytest.mark.parametrize("text", ["", "Commit succeeded\n", "Revision  : x\n", "Revision : \n"])
def test_committed_revision_is_none_when_the_line_is_missing_or_odd(text: str) -> None:
    assert parsers.committed_revision(text) is None


def test_pushed_revision_reads_revision_and_branch() -> None:
    assert parsers.pushed_revision(PUSH_OUT) == parsers.PushedRevision(3, "main")


def test_pushed_revision_takes_the_last_of_several() -> None:
    text = f"Pushed revision 3 -> {SIG} to branch dev\nPushed revision 4 -> {SIG} to branch dev\n"
    assert parsers.pushed_revision(text) == parsers.PushedRevision(4, "dev")


@pytest.mark.parametrize("text", ["", "Pushed 1 fragment(s)\n", "Pushed revision 3 to branch main\n"])
def test_pushed_revision_is_none_when_the_line_is_missing_or_odd(text: str) -> None:
    assert parsers.pushed_revision(text) is None
