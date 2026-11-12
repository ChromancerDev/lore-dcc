"""Errors raised by the plugin core."""

from __future__ import annotations


class LoreError(Exception):
    """Base class for every error the plugin reports to the user."""


class NotAWorkingCopy(LoreError):
    """The file is not inside a Lore working copy."""


class LoreNotFound(LoreError):
    """The `lore` executable is not on PATH."""


class LoreVersionError(LoreError):
    """The installed Lore is older than the plugin supports."""


class SettingsError(LoreError):
    """A `lore-dcc.json` file is malformed."""


class LoreCommandError(LoreError):
    """A Lore command exited non-zero or ran past its deadline.

    Attributes:
        args_used: The argv that ran.
        returncode: The exit code, or None on a timeout.
        output: Combined stdout and stderr, for display.
    """

    def __init__(
        self, message: str, args_used: list[str], returncode: int | None, output: str
    ) -> None:
        super().__init__(message)
        self.args_used = args_used
        self.returncode = returncode
        self.output = output


class ServerUnreachable(LoreCommandError):
    """Lore could not reach the server, so nothing that needs the server ran."""

    def __init__(self, args_used: list[str], returncode: int | None, output: str) -> None:
        super().__init__(SERVER_UNREACHABLE_TEXT, args_used, returncode, output)


SERVER_UNREACHABLE_TITLE = "Lore - Can't Reach the Server"
SERVER_UNREACHABLE_TEXT = (
    "Lore can't reach the server. Check your network or VPN, then try again.\n\n"
    "Locking, lock checks and commits wait until the server answers."
)


class LockNotAcquired(LoreError):
    """Lore exited cleanly but did not report holding every requested lock.

    Attributes:
        paths: Repo-relative paths that are not held.
        output: Lore's output, for display.
    """

    def __init__(self, paths: list[str], output: str) -> None:
        super().__init__(f"Could not lock {len(paths)} file(s): {', '.join(paths[:5])}")
        self.paths = paths
        self.output = output


class LoreOutputError(LoreError):
    """Lore printed text the parser does not recognise.

    Attributes:
        raw: The full output that failed to parse.
    """

    def __init__(self, message: str, raw: str) -> None:
        super().__init__(message)
        self.raw = raw
