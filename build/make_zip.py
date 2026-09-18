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
import json
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


def blank_site_config(folder: Path) -> None:
    """Replace the build machine's settings with a config that is honestly unconfigured.

    What used to ship was whatever happened to be in the repository: video files off this
    laptop, four leftover test zones, placeholder camera IPs. All of it looks plausible
    and none of it is the site's, so the pre-flight reported a healthy system that could
    not see anything. A blank config fails the pre-flight on the first run, loudly and
    with the reason - which is the whole point of having one.

    PLC simulation stays on. Nothing should write to a real PLC until somebody has looked
    at the addresses; the pre-flight warns about it every single start.
    """
    def edit(name: str, change) -> None:
        path = folder / name
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        change(data)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def cameras(data: dict) -> None:
        data["count"] = 1
        data["extra"] = []
        data["camera_type"] = "rtsp"
        for section in ("video",):
            data.setdefault(section, {})["path"] = ""
        for section in ("rtsp", "ai_camera"):
            block = data.setdefault(section, {})
            block["ip"] = ""
            block["username"] = ""
            block["password"] = ""
            if "url" in block:
                block["url"] = ""
            if "rtsp_url" in block:
                block["rtsp_url"] = ""

    def plc(data: dict) -> None:
        data["simulation_mode"] = True
        # The PLC address was a guess about the customer's network - the same kind of
        # plausible-looking default that made the camera checks pass on nothing. Blank
        # it: with no address the pre-flight asks for one, instead of sending MC frames
        # to whatever else happens to live at 192.168.1.10.
        data.setdefault("connection", {})["ip"] = ""

    edit("camera_config.json", cameras)
    edit("roi_config.json", lambda d: d.update({"rois": []}))
    edit("plc_config.json", plc)
    print("  config blanked for the site: 1 camera, no IP, no zones, PLC in simulation",
          flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venv", default="venv-gpu", help="build environment under build/")
    ap.add_argument("--name", default="GPU", help="label used in the zip name")
    ap.add_argument("--skip-build", action="store_true", help="reuse the existing bundle")
    ap.add_argument("--model", default=None,
                    help="detector to ship in the package's own config, e.g. yolo11s.pt. "
                         "The repository's config is not touched - a GPU package wants a "
                         "different model from the machine it was built on.")
    ap.add_argument("--keep-dev-config", action="store_true",
                    help="ship this machine's config as-is instead of a blank site config")
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

    if not args.keep_dev_config:
        blank_site_config(bundle / "config")

    if args.model:
        # The machine that builds and the machine that runs want different detectors.
        # Measured on a GTX 1650 over the same 60 frames: yolo11s costs the same 14ms as
        # yolo11n but loses the person in 7 frames instead of 13 - same price, misses
        # half as often. On a CPU box that swap doubles the frame time, which is why it
        # is set here per package and not in the repository's config.
        path = bundle / "config" / "ai_config.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data.setdefault("detector", {})["model_path"] = f"models/{args.model}"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  config in the package now points at models/{args.model}", flush=True)
        if not (bundle / "models" / args.model).exists():
            raise SystemExit(f"models/{args.model} is not in the bundle - nothing would load")

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
