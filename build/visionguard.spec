# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: VisionGuard.exe plus the launcher that supervises it.

One folder, not one file. A one-file build of this size would unpack several hundred
megabytes into a temp folder on every single launch - on a machine that restarts after a
power cut that is the worst possible trade.

Torch is the CPU build on purpose. The CUDA wheel is 4.4 GB against 526 MB and still
demands an NVIDIA driver on the target; CPU inference was measured at 14-18 fps, which is
comfortably above the 12 fps the detector is capped at. A site that wants the GPU installs
from source instead.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

datas = []
# Ultralytics reads yaml out of its own package at runtime (default.yaml, trackers).
datas += collect_data_files("ultralytics", include_py_files=False)

# torchvision registers its custom operators (nms among them) from a compiled extension.
# PyInstaller does not pick that up on its own, and without it the model loads and then
# dies at the first inference with "operator torchvision::nms does not exist".
binaries = []
binaries += collect_dynamic_libs("torchvision")
binaries += collect_dynamic_libs("torch")

# collect_dynamic_libs finds the .dll files but not the .pyd extension modules beside
# them, and those are the ones that register the operators. torchvision 0.29 names its
# extension _C_stable.pyd rather than _C.pyd, so glob rather than name it.
import torchvision as _tv

_TV = Path(_tv.__file__).parent
binaries += [(str(f), "torchvision") for f in _TV.glob("*.pyd")]
binaries += [(str(f), "torchvision/io/image") for f in (_TV / "io" / "image").glob("*.pyd")]
datas += collect_data_files("torchvision", include_py_files=False)

hiddenimports = []
hiddenimports += collect_submodules("visionguard")
hiddenimports += ["ultralytics.utils.checks", "ultralytics.nn.tasks"]
hiddenimports += ["torchvision", "torchvision.ops", "torchvision.ops.boxes",
                  "torchvision.transforms", "torchvision.io"]

# Qt modules this app never touches; each one is tens of megabytes.
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtDesigner", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtTest", "PySide6.QtSql",
    # NOTE: unittest/pydoc/doctest stay in - ultralytics imports them at import time,
    # and excluding them fails the model load with a misleading "not installed".
    "tkinter", "pytest", "IPython", "jupyter", "notebook",
]

app = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

launcher = Analysis(
    [str(ROOT / "tools" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules("visionguard.diagnostics"),
    excludes=excludes,
    noarchive=False,
)

MERGE((app, "VisionGuard", "VisionGuard"),
      (launcher, "VisionGuardLauncher", "VisionGuardLauncher"))

app_pyz = PYZ(app.pure)
launcher_pyz = PYZ(launcher.pure)

app_exe = EXE(
    app_pyz, app.scripts, [],
    exclude_binaries=True,
    name="VisionGuard",
    debug=False,
    console=False,               # the operator sees the window, not a terminal
    disable_windowed_traceback=False,
    icon=None,
)

launcher_exe = EXE(
    launcher_pyz, launcher.scripts, [],
    exclude_binaries=True,
    name="VisionGuardLauncher",
    debug=False,
    console=True,                # it is the watchdog; its log is the point
    icon=None,
)

COLLECT(
    app_exe, app.binaries, app.datas,
    launcher_exe, launcher.binaries, launcher.datas,
    strip=False,
    upx=False,                   # UPX on Qt/torch DLLs is a reliable way to break them
    name="VisionGuard",
)
