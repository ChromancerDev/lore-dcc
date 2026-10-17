"""Stand-ins for the parts of Designer's `sd` API the plugin calls.

`FakeApp` mirrors SDApplication, SDPackageMgr, SDUIMgr and the Qt UI manager.
Each test builds its packages and resources.
"""

from __future__ import annotations

import types
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class EmbedMethod(Enum):
    Undefined = 0
    Linked = 1
    CopiedAndLinked = 2
    BinaryEmbedded = 3


@dataclass
class FakeResource:
    identifier: str
    file: str
    method: EmbedMethod = EmbedMethod.Linked

    def getIdentifier(self) -> str:
        return self.identifier

    def getFilePath(self) -> str:
        return self.file

    def getEmbedMethod(self) -> EmbedMethod:
        return self.method


class SDResourceBitmap(FakeResource):
    pass


@dataclass
class FakeDependency:
    file: str

    def getFilePath(self) -> str:
        return self.file


@dataclass
class FakePackage:
    file: str
    dependencies: list[FakeDependency] = field(default_factory=list)
    resources: list[FakeResource] = field(default_factory=list)
    modified: bool = False

    def getFilePath(self) -> str:
        return self.file

    def getDependencies(self) -> list[FakeDependency]:
        return self.dependencies

    def getChildrenResources(self, recursive: bool) -> list[FakeResource]:
        assert recursive
        return self.resources

    def isModified(self) -> bool:
        return self.modified


@dataclass
class FakeGraph:
    package: FakePackage

    def getPackage(self) -> FakePackage:
        return self.package

    def getIdentifier(self) -> str:
        return "Substance_graph"


class FakePackageMgr:
    def __init__(self, app: FakeApp) -> None:
        self.app = app

    def getUserPackages(self) -> list[FakePackage]:
        return list(self.app.packages)

    def unloadUserPackage(self, package: FakePackage) -> None:
        self.app.events.append(("unload", package.file))
        self.app.packages.remove(package)

    def loadUserPackage(self, path: str) -> FakePackage:
        self.app.events.append(("load", path))
        package = FakePackage(path)
        self.app.packages.append(package)
        return package


class FakeUIMgr:
    def __init__(self, app: FakeApp) -> None:
        self.app = app

    def getCurrentGraph(self) -> FakeGraph | None:
        return FakeGraph(self.app.current) if self.app.current else None


class FakeMenu:
    def __init__(self) -> None:
        self.entries: list[Any] = []

    def addSeparator(self) -> None:
        self.entries.append(None)

    def addAction(self, label: str) -> Any:
        action = types.SimpleNamespace(label=label, slots=[])
        action.triggered = types.SimpleNamespace(connect=action.slots.append)
        self.entries.append(action)
        return action


class FakeQtUI:
    def __init__(self, app: FakeApp) -> None:
        self.app = app
        self.menus: dict[str, FakeMenu] = {}
        self.callbacks: dict[int, Any] = {}
        self.selection: list[Any] = []

    def getMainWindow(self) -> None:
        return None

    def newMenu(self, title: str, object_name: str) -> FakeMenu:
        self.menus[object_name] = FakeMenu()
        return self.menus[object_name]

    def findMenuFromObjectName(self, object_name: str) -> FakeMenu | None:
        return self.menus.get(object_name)

    def deleteMenu(self, object_name: str) -> None:
        self.menus.pop(object_name)

    def registerExplorerCreatedCallback(self, callback: Any) -> int:
        return self.app.register(callback, "explorer")

    def unregisterCallback(self, callback_id: int) -> None:
        self.app.unregisterCallback(callback_id)

    def getExplorerSelection(self, explorer_id: int) -> list[Any]:
        return self.selection


class FakePluginMgr:
    def __init__(self, folder: str) -> None:
        self.folder = folder

    def getUserPluginsDir(self) -> str:
        return self.folder


class FakeApp:
    def __init__(self, user_folder: Path) -> None:
        self.packages: list[FakePackage] = []
        self.current: FakePackage | None = None
        self.events: list[tuple[str, str]] = []
        self.callbacks: dict[int, tuple[str, Any]] = {}
        self.ui: FakeQtUI | None = FakeQtUI(self)
        self.plugin_mgr = FakePluginMgr(str(user_folder / "python" / "sduserplugins"))

    def register(self, callback: Any, kind: str) -> int:
        callback_id = len(self.callbacks) + 1
        self.callbacks[callback_id] = (kind, callback)
        return callback_id

    def unregisterCallback(self, callback_id: int) -> None:
        del self.callbacks[callback_id]

    def callback(self, kind: str) -> Any:
        return next(cb for k, cb in self.callbacks.values() if k == kind)

    def registerBeforeFileSavedCallback(self, callback: Any) -> int:
        return self.register(callback, "before_save")

    def registerAfterFileSavedCallback(self, callback: Any) -> int:
        return self.register(callback, "after_save")

    def registerAfterFileLoadedCallback(self, callback: Any) -> int:
        return self.register(callback, "after_load")

    def getPackageMgr(self) -> FakePackageMgr:
        return FakePackageMgr(self)

    def getUIMgr(self) -> FakeUIMgr:
        return FakeUIMgr(self)

    def getQtForPythonUIMgr(self) -> FakeQtUI | None:
        return self.ui

    def getPluginMgr(self) -> FakePluginMgr:
        return self.plugin_mgr

    def getVersion(self) -> str:
        return "16.0.7"


INSTALL = Path(__file__).resolve().parent / "fake_designer_install"
