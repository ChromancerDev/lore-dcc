"""The Info and Commit dialogs."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from PySide6 import QtCore, QtWidgets

from lore_dcc import commit_model, prefs
from lore_dcc.backend.parsers import Status
from lore_dcc.commit_model import CommitRow
from lore_dcc.errors import LoreCommandError, LoreError
from lore_dcc.session import CommitResult, Session
from lore_dcc.ui import parent_window, prefs_folder
from lore_dcc.ui.messages import show_error, show_message
from lore_dcc.ui.tasks import run_blocking

log = logging.getLogger(__name__)

_CHECK = QtCore.Qt.CheckState
_ITEM = QtCore.Qt.ItemFlag

_STATUS_TOOLTIPS = {
    "M": "Modified. Perforce calls this an edit.",
    "A": "Added. Perforce calls this an add.",
    "D": "Deleted. Perforce calls this a delete.",
}


@dataclass(frozen=True)
class _InfoData:
    status: Status
    identity_text: str
    version_text: str


def _load_info(session: Session) -> _InfoData:
    status = session.status()
    try:
        identity = session.identity()
        try:
            identity_text = session.lore.auth_info().name
        except LoreCommandError:
            identity_text = ", ".join(sorted(identity.names))
    except LoreCommandError:
        hint = "lore login"
        if session.workspace.remote_url:
            hint = f"lore login {session.workspace.remote_url}"
        identity_text = f"Not signed in. Run in a terminal: {hint}"
    try:
        major, minor, patch = session.lore.version()
        version_text = f"{major}.{minor}.{patch}"
    except LoreError:
        version_text = "Unknown"
    return _InfoData(status, identity_text, version_text)


class _InfoDialog(QtWidgets.QDialog):
    def __init__(self, session: Session, data: _InfoData, parent: QtWidgets.QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lore info")
        self.setMinimumWidth(480)
        self.values: dict[str, QtWidgets.QLabel] = {}
        status = data.status
        rows = (
            ("Working copy", str(session.workspace.root)),
            ("Branch", status.branch),
            ("Revision", str(status.revision)),
            ("Remote state", commit_model.remote_state_text(status.remote_state)),
            ("Remote URL", session.workspace.remote_url or "Not set"),
            ("Identity", data.identity_text),
            ("Lore version", data.version_text),
        )
        form = QtWidgets.QFormLayout()
        for name, value in rows:
            label = QtWidgets.QLabel(value)
            label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            self.values[name] = label
            form.addRow(name, label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)


def show_info(session: Session) -> None:
    """Show working copy, branch, identity, remote, remote state and Lore version.

    When `lore auth info` fails, show the `lore login <remote_url>` hint instead
    of an error.
    """
    try:
        data = run_blocking(_load_info, session, title="Reading Lore info")
    except LoreError as exc:
        show_error(exc, "Could not read Lore info")
        return
    _InfoDialog(session, data, parent_window()).exec()


def _load_prefs() -> prefs.Prefs:
    folder = prefs_folder()
    return prefs.load_prefs(folder) if folder else prefs.Prefs()


class _CommitDialog(QtWidgets.QDialog):
    """Commit dialog. `outcome` holds the last CommitResult that made a revision."""

    def __init__(
        self,
        session: Session,
        status: Status,
        rows: list[CommitRow],
        user_prefs: prefs.Prefs,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Commit")
        self.resize(780, 600)
        self._session = session
        self._rows = rows
        self._items: list[QtWidgets.QTreeWidgetItem] = []
        self._group_items: list[QtWidgets.QTreeWidgetItem] = []
        self._updating = False
        self._push_requested = user_prefs.push_after_commit
        self.outcome: CommitResult | None = None

        remote = commit_model.remote_state_text(status.remote_state)
        self.branch_label = QtWidgets.QLabel(f"Branch {status.branch}  |  {remote}")
        self.branch_label.setToolTip(
            "Behind remote does not block a commit. Sync before you push if the push is refused."
        )

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Status", "Path", "Lock"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 150)
        self.tree.setColumnWidth(2, 170)

        self.banner = QtWidgets.QLabel()
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)

        self.summary_edit = QtWidgets.QLineEdit()
        self.summary_edit.setPlaceholderText("Summary (required)")
        self.description_edit = QtWidgets.QPlainTextEdit()
        self.description_edit.setPlaceholderText("Description (optional)")
        self.description_edit.setFixedHeight(80)

        self.push_box = QtWidgets.QCheckBox("Push after commit")
        self.push_box.setChecked(user_prefs.push_after_commit)
        self.push_box.setToolTip(
            "Commit records a revision locally. Push sends it to the server. "
            "Together they are what Perforce calls Submit."
        )
        self.keep_box = QtWidgets.QCheckBox("Keep files locked")
        self.keep_box.setChecked(user_prefs.keep_files_locked)
        self.keep_box.setToolTip("Keep your locks after the push. Perforce: keep files checked out.")

        self.hint_label = QtWidgets.QLabel()
        self.count_label = QtWidgets.QLabel()
        self.commit_button = QtWidgets.QPushButton("Commit")
        self.commit_button.setDefault(True)
        self.retry_button = QtWidgets.QPushButton("Retry push")
        self.retry_button.setVisible(False)
        self.cancel_button = QtWidgets.QPushButton("Cancel")

        footer = QtWidgets.QHBoxLayout()
        footer.addWidget(self.count_label)
        footer.addWidget(self.hint_label, 1)
        footer.addWidget(self.commit_button)
        footer.addWidget(self.retry_button)
        footer.addWidget(self.cancel_button)
        options = QtWidgets.QHBoxLayout()
        options.addWidget(self.push_box)
        options.addWidget(self.keep_box)
        options.addStretch(1)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.branch_label)
        layout.addWidget(self.banner)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.summary_edit)
        layout.addWidget(self.description_edit)
        layout.addLayout(options)
        layout.addLayout(footer)

        self._populate()
        self.tree.itemChanged.connect(self._on_item_changed)
        self.summary_edit.textChanged.connect(self._refresh)
        self.commit_button.clicked.connect(self._on_commit)
        self.retry_button.clicked.connect(self._on_retry)
        self.cancel_button.clicked.connect(self.reject)
        self._refresh()
        self.summary_edit.setFocus()

    def _populate(self) -> None:
        self._updating = True
        parents: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for row in self._rows:
            parent = parents.get(row.group)
            if parent is None:
                parent = QtWidgets.QTreeWidgetItem(self.tree, [row.group])
                parent.setFlags(_ITEM.ItemIsEnabled | _ITEM.ItemIsUserCheckable)
                parents[row.group] = parent
                self._group_items.append(parent)
            item = QtWidgets.QTreeWidgetItem(parent, [row.status_word, row.path, row.lock_text])
            item.setToolTip(0, _STATUS_TOOLTIPS.get(row.code, ""))
            item.setToolTip(1, row.path)
            if row.enabled:
                item.setFlags(_ITEM.ItemIsEnabled | _ITEM.ItemIsUserCheckable)
                item.setCheckState(0, _CHECK.Checked if row.checked else _CHECK.Unchecked)
            else:
                item.setFlags(_ITEM.NoItemFlags)
                item.setToolTip(2, row.reason)
            self._items.append(item)
        for parent in self._group_items:
            parent.setText(0, f"{parent.text(0)} ({parent.childCount()})")
            self._sync_group(parent)
            parent.setExpanded(True)
        self._updating = False

    def _sync_group(self, parent: QtWidgets.QTreeWidgetItem) -> None:
        children = [
            parent.child(i)
            for i in range(parent.childCount())
            if parent.child(i).flags() & _ITEM.ItemIsEnabled
        ]
        if not children:
            parent.setFlags(_ITEM.ItemIsEnabled)
            return
        checked = sum(1 for c in children if c.checkState(0) == _CHECK.Checked)
        if checked == 0:
            state = _CHECK.Unchecked
        elif checked == len(children):
            state = _CHECK.Checked
        else:
            state = _CHECK.PartiallyChecked
        parent.setCheckState(0, state)

    def _on_item_changed(self, item: QtWidgets.QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        self._updating = True
        try:
            parent = item.parent()
            if parent is None:
                state = item.checkState(0)
                if state != _CHECK.PartiallyChecked:
                    for i in range(item.childCount()):
                        child = item.child(i)
                        if child.flags() & _ITEM.ItemIsEnabled:
                            child.setCheckState(0, state)
                self._sync_group(item)
            else:
                self._sync_group(parent)
        finally:
            self._updating = False
        self._refresh()

    def checked_paths(self) -> list[str]:
        """Return the repo-relative paths of every checked, enabled row."""
        return [
            row.path
            for row, item in zip(self._rows, self._items, strict=True)
            if row.enabled and item.checkState(0) == _CHECK.Checked
        ]

    def _refresh(self) -> None:
        count = len(self.checked_paths())
        self.count_label.setText(commit_model.files_label(count))
        hint = commit_model.commit_hint(self.summary_edit.text(), count)
        self.hint_label.setText(hint)
        self.commit_button.setEnabled(not hint)

    def _run(
        self, title: str, fn: Callable[..., CommitResult], *args: object
    ) -> CommitResult | None:
        self.setEnabled(False)
        try:
            return run_blocking(fn, *args, title=title)
        except Exception as exc:  # shown to the user, dialog stays open
            log.exception("%s failed", title)
            show_error(exc, f"{title} failed")
            return None
        finally:
            self.setEnabled(True)

    def _on_commit(self) -> None:
        include = self.checked_paths()
        summary = self.summary_edit.text().strip()
        if commit_model.commit_hint(summary, len(include)):
            return
        self._push_requested = self.push_box.isChecked()
        result = self._run(
            "Commit",
            self._session.commit,
            include,
            summary,
            self.description_edit.toPlainText().strip(),
            self._push_requested,
            self.keep_box.isChecked(),
        )
        if result is not None:
            self._apply_result(result)

    def _on_retry(self) -> None:
        result = self._run("Push", self._session.retry_push)
        if result is not None:
            self._apply_result(result)

    def _apply_result(self, result: CommitResult) -> None:
        if not result.committed:
            show_message("Commit", result.output or "Nothing was committed.")
            return
        self.outcome = result
        push_failed = not result.pushed and (self._push_requested or result.push_error is not None)
        if push_failed:
            self._enter_retry_state(result)
        else:
            self.accept()

    def _enter_retry_state(self, result: CommitResult) -> None:
        self.banner.setText(commit_model.push_failure_text(result))
        self.banner.setToolTip(result.push_error or "")
        self.banner.setVisible(True)
        for widget in (
            self.tree,
            self.summary_edit,
            self.description_edit,
            self.push_box,
            self.keep_box,
        ):
            widget.setEnabled(False)
        self.commit_button.setVisible(False)
        self.retry_button.setVisible(True)
        self.retry_button.setDefault(True)
        self.hint_label.setText("")
        self.cancel_button.setText("Close")


def show_commit(session: Session) -> CommitResult | None:
    """Show the modal Commit dialog. Returns the result, or None if cancelled.

    Groups: This session (session.touched), Outputs (session.outputs), Other
    changes, Cannot commit (FileState.blocked, disabled with the reason).

    If the revision was made but the push failed and the user closes the
    dialog, the committed-but-not-pushed result is returned so the caller knows
    a local revision exists.
    """
    try:
        status, rows = run_blocking(commit_model.load_rows, session, title="Reading changes")
    except LoreError as exc:
        show_error(exc, "Could not read changes")
        return None
    if not rows:
        show_message("Commit", "There are no changes to commit.")
        return None
    dialog = _CommitDialog(session, status, rows, _load_prefs(), parent_window())
    dialog.exec()
    return dialog.outcome
