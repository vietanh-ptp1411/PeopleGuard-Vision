"""Build a ready-to-run folder and zip it - used for the GPU package.

Why a zip rather than the one-file installer the CPU build ships as: with CUDA the bundle
is around 5 GB, and a one-file PyInstaller installer would have to unpack all of it into a
temp folder every time it is run. A zip plus Install.bat is honest about what it is and
costs the customer one extraction.

    build\\venv-gpu\\Scripts\\python.exe build\\make_zip.py --venv venv-gpu --name GPU
    build\\venv\\Scripts\\python.exe     build\\make_zip.py --venv venv     --name CPU
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

BUILD = Path(__file__).resolve().parent
ROOT = BUILD.parent
OUT = ROOT / "release"


def run(*cmd: str) -> None:
    print("  $", " ".join(str(c) for c in cmd), flush=True)
    if subprocess.run([str(c) for c in cmd], cwd=str(ROOT)).returncode != 0:
        raise SystemExit("build step failed")


def folder_gb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e9


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", default="venv-gpu", help="build environment under build/")
    ap.add_argument("--name", default="GPU", help="label used in the zip name")
    ap.add_argument("--skip-build", action="store_true", help="reuse the existing bundle")
    args = ap.parse_args()

    started = time.time()
    dist = BUILD / f"dist-{args.name.lower()}"
    work = BUILD / f"work-{args.name.lower()}"
    bundle = dist / "VisionGuard"
    python = BUILD / args.venv / "Scripts" / "python.exe"
    if not python.exists():
        raise SystemExit(f"no build environment at {python}")

    if not args.skip_build:
        print(f"\n=== 1/3  Building the {args.name} bundle ===", flush=True)
        shutil.rmtree(dist, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
        run(python, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", dist, "--workpath", work, BUILD / "visionguard.spec")
    if not (bundle / "VisionGuard.exe").exists():
        raise SystemExit("VisionGuard.exe was not produced")
    print(f"  bundle: {folder_gb(bundle):.2f} GB", flush=True)

    print("\n=== 2/3  Adding config, model and the installer script ===", flush=True)
    for name in ("config", "models"):
        src = ROOT / name
        if src.exists():
            shutil.copytree(src, bundle / name, dirs_exist_ok=True)
    shutil.copy2(BUILD / "Install.bat", bundle / "Install.bat")
    shutil.copy2(ROOT / "tools" / "README-TRIEN-KHAI.md", bundle / "HUONG-DAN.md")
    for junk in ("logs", "events"):
        shutil.rmtree(bundle / junk, ignore_errors=True)

    print("\n=== 3/3  Zipping ===", flush=True)
    OUT.mkdir(exist_ok=True)
    target = OUT / f"VisionGuard-{args.name}.zip"
    target.unlink(missing_ok=True)
    files = [f for f in bundle.rglob("*") if f.is_file()]
    # Deflate at a low level: these are already-compressed DLLs, and level 9 would spend
    # ten minutes to save a couple of percent.
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as z:
        for i, f in enumerate(files, 1):
            z.write(f, Path("VisionGuard") / f.relative_to(bundle))
            if i % 1000 == 0:
                print(f"    {i}/{len(files)}", flush=True)

    size_gb = target.stat().st_size / 1e9
    print(f"\n  {target}  ({size_gb:.2f} GB, {len(files)} files)", flush=True)
    print(f"Done in {(time.time() - started) / 60:.1f} minutes.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
