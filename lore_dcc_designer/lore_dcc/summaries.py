"""Message texts every DCC's actions share. Stdlib only."""

from __future__ import annotations

from collections.abc import Sequence

from lore_dcc.session import BehindReport, SyncResult, WriteResult

LISTED = 8


def list_paths(paths: Sequence[str]) -> str:
    """Return `paths` as indented lines, capped at a few with a count of the rest."""
    lines = [f"    {p}" for p in paths[:LISTED]]
    if len(paths) > LISTED:
        lines.append(f"    and {len(paths) - LISTED} more")
    return "\n".join(lines)


def with_notes(text: str, notes: Sequence[str]) -> str:
    """Return `text` followed by one `Warning:` line per note."""
    return "\n".join([text, *(f"Warning: {note}" for note in notes)])


def describe_write(result: WriteResult) -> str:
    """Return a short summary of what finish_write did."""
    lines: list[str] = []
    for label, paths in (
        ("Staged (new)", result.staged),
        ("Changed (lock kept)", result.changed),
        ("Released (unchanged)", result.released),
    ):
        if paths:
            lines.append(f"{label}: {len(paths)}")
            lines.append(list_paths(paths))
    return "\n".join(lines) or "No output files changed."


def scene_behind_text(report: BehindReport, edited: bool) -> str:
    """Return the warning for a scene that changed on the server."""
    parts = [
        (
            "The open scene is behind the server: a newer revision of it was pushed. "
            "Saving and committing this copy would overwrite that version."
        )
    ]
    if report.behind:
        parts.append(f"These references are also behind:\n{list_paths(report.behind)}")
    if edited:
        parts.append(
            "This scene has local changes, so it cannot be synced. "
            "Continue syncs only the references."
        )
    else:
        parts.append(
            "Continue syncs only the references. "
            "Sync and reopen also syncs the scene and reopens it."
        )
    parts.append(
        "After Continue, Lore counts this scene as up to date and you will not be warned again. "
        "Use Discard changes to get the server version."
    )
    return "\n\n".join(parts)


def sync_summary(result: SyncResult, scene_skipped: bool) -> str:
    """Return the report after Sync Dependencies."""
    parts = [
        f"Synced {len(result.synced)} file(s):\n{list_paths(result.synced)}"
        if result.synced
        else "Nothing was synced."
    ]
    if result.skipped:
        parts.append(
            f"Skipped:\n{list_paths(result.skipped)}\n"
            "These have local changes, so Lore left them as they are. Discard changes on a "
            "file to get the server version. Committing it writes over the newer version "
            "on the server."
        )
    if scene_skipped:
        parts.append("The scene was not synced or reopened.")
    return "\n\n".join(parts)


def behind_offer_text(report: BehindReport) -> str:
    """Return the text of the after-load offer to sync dependencies."""
    parts = []
    if report.source_behind:
        parts.append("This scene itself is behind the server.")
    if report.behind:
        parts.append(f"These references are behind the server:\n{list_paths(report.behind)}")
    return "\n\n".join(parts)


def outside_text(outside: Sequence[tuple[str, str]]) -> str:
    """Return the warning for references outside the working copy."""
    where = list_paths([f"{where}: {file}" for where, file in outside])
    return (
        "These files are outside the working copy, so other users will not get them:\n"
        f"{where}\n\nThe scene still commits."
    )
