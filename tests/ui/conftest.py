"""Shared fixtures for the Qt tests. Each test module skips itself without PySide6."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def qapp():
    """Return the one QApplication for the test run."""
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _reset_ui_globals():
    """Restore the module-level parent provider after each test."""
    from lore_dcc import ui

    yield
    ui._parent_provider = None
