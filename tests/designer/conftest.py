"""Give pure-Python tests a stand-in `sd` package outside Designer."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fake_sd import INSTALL, FakeApp

try:
    import sd  # noqa: F401
except ImportError:
    stub = types.ModuleType("sd")
    stub.__file__ = str(INSTALL / "resources" / "python" / "sd" / "__init__.py")
    stub.getContext = lambda: types.SimpleNamespace(getSDApplication=lambda: stub.app)
    stub.app = None
    sys.modules["sd"] = stub


@pytest.fixture
def app(tmp_path: Path) -> FakeApp:
    import sd

    fake = FakeApp(tmp_path / "designer_user")
    sd.app = fake
    return fake
