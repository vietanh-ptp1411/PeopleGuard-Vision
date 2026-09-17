"""Build VisionGuard-Setup.exe - the whole pipeline, repeatable.

    build\\venv\\Scripts\\python.exe build\\make_installer.py

Three stages:

    1. PyInstaller turns the source into build/dist/VisionGuard/ - the application and its
       launcher sharing one set of libraries.
    2. That folder, plus a default config and the YOLO weights, is zipped.
    3. PyInstaller turns setup_app.py into a single exe with the zip inside it, so the
       customer machine needs nothing but the one file.

The build environment deliberately has the CPU build of torch. The CUDA wheel is 4.4 GB
against 526 MB and still needs an NVIDIA driver on the target; CPU inference measured
14-18 fps, comfortably above the 12 fps cap the detector runs at.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

BUILD = Path(__file__).resolve().parent
ROOT = BUILD.parent
DIST = BUILD / "dist"
WORK = BUILD / "work"
BUNDLE = DIST / "VisionGuard"
PAYLOAD = BUILD / "payload.zip"
OUT = ROOT / "release"


def run(*cmd: str) -> None:
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    result = subprocess.run([str(c) for c in cmd], cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"Failed: {' '.join(str(c) for c in cmd)}")


def folder_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def stage_app() -> None:
    print("\n=== 1/3  Building the application bundle ===", flush=True)
    for d in (DIST, WORK):
        shutil.rmtree(d, ignore_errors=True)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", WORK, BUILD / "visionguard.spec")
    if not (BUNDLE / "VisionGuard.exe").exists():
        raise SystemExit("VisionGuard.exe was not produced")
    print(f"  bundle: {folder_mb(BUNDLE):.0f} MB", flush=True)


def stage_payload() -> None:
    print("\n=== 2/3  Packing the payload ===", flush=True)
    # A clean default config ships with the installer; an existing site keeps its own.
    staging = BUILD / "staging"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(BUNDLE, staging)
    for name in ("config", "models"):
        src = ROOT / name
        if src.exists():
            shutil.copytree(src, staging / name, dirs_exist_ok=True)
    # never ship the site's own recordings or history
    for junk in ("logs", "events", "release"):
        shutil.rmtree(staging / junk, ignore_errors=True)
    for stale in staging.rglob("*.tmp"):
        stale.unlink(missing_ok=True)

    PAYLOAD.unlink(missing_ok=True)
    files = [f for f in staging.rglob("*") if f.is_file()]
    with zipfile.ZipFile(PAYLOAD, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for i, f in enumerate(files, 1):
            z.write(f, f.relative_to(staging))
            if i % 500 == 0:
                print(f"    {i}/{len(files)}", flush=True)
    shutil.rmtree(staging, ignore_errors=True)
    print(f"  payload: {PAYLOAD.stat().st_size / 1e6:.0f} MB ({len(files)} files)", flush=True)


def stage_installer() -> None:
    print("\n=== 3/3  Building the installer ===", flush=True)
    OUT.mkdir(exist_ok=True)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
        "--console", "--name", "VisionGuard-Setup",
        "--distpath", OUT, "--workpath", WORK / "setup", "--specpath", WORK,
        "--add-data", f"{PAYLOAD};.",
        BUILD / "setup_app.py")
    exe = OUT / "VisionGuard-Setup.exe"
    if not exe.exists():
        raise SystemExit("the installer was not produced")
    print(f"\n  {exe}  ({exe.stat().st_size / 1e6:.0f} MB)", flush=True)


def main() -> int:
    started = time.time()
    stage_app()
    stage_payload()
    stage_installer()
    print(f"\nDone in {(time.time() - started) / 60:.1f} minutes.", flush=True)
    print(f"Ship this one file: {OUT / 'VisionGuard-Setup.exe'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
