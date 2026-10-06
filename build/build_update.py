"""Build, test and package a small cumulative customer update with one command."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGES = ("PySide6", "shiboken6", "opencv-python", "numpy", "torch", "torchvision",
                    "ultralytics", "psutil", "requests", "pyinstaller", "pyinstaller-hooks-contrib")


def atomic_json(path: Path, data) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_hashes() -> dict[str, str]:
    paths = [*(ROOT / "visionguard").rglob("*.py"),
             *(ROOT / "visionguard/app/assets").glob("*.png"),
             ROOT / "main.py", ROOT / "tools/launcher.py", ROOT / "build/visionguard.spec",
             ROOT / "build/update.ps1", ROOT / "build/make_update.py", ROOT / "build/vgdelta.py"]
    return {p.relative_to(ROOT).as_posix(): file_hash(p) for p in sorted(paths)}


def recover_publications(output: Path, base: Path, history_path: Path) -> list[str]:
    """Finish only verified publications interrupted between ZIP and history writes."""
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    for journal in sorted(output.glob(".update-publish-*.json")):
        pending = json.loads(journal.read_text(encoding="utf-8"))
        name = pending["package"]
        if (Path(name).name != name or "/" in name or "\\" in name or
                not name.startswith("VisionGuard-GPU-Update-") or not name.endswith(".zip")):
            raise ValueError(f"Invalid publication journal: {journal}")
        package = output / name
        if not package.exists():
            # A crash before the ZIP rename never exposed a customer release.
            journal.unlink()
            continue
        if Path(pending["baseline"]).resolve() != base.resolve():
            raise ValueError(f"Interrupted publication belongs to another baseline: {journal}")
        if file_hash(package) != pending["sha256"]:
            raise ValueError(f"Interrupted publication checksum mismatch: {package}")
        # The journal is written only after verification; unrelated ZIPs are never adopted.
        if str(package) not in history:
            history.append(str(package))
        atomic_json(history_path, history)
        journal.unlink()
        print(f"Recovered verified publication: {package.name}", flush=True)
    return history


def publish_verified(staged: Path, target: Path, base: Path, history_path: Path,
                     history: list[str]) -> None:
    journal = target.parent / f".update-publish-{target.stem}.json"
    atomic_json(journal, {"package": target.name, "baseline": str(base.resolve()),
                          "sha256": file_hash(staged)})
    for suffix in (".zip.sha256", ".manifest.json", ".validation.json"):
        staged.with_suffix(suffix).replace(target.with_suffix(suffix))
    staged.replace(target)
    atomic_json(history_path, [*history, str(target)])
    journal.unlink()


def build_environment() -> dict:
    env = os.environ.copy()
    # Git's OpenSSL DLLs must not replace Python's DLLs in the customer bundle.
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                 if not any(part.lower() == "git" for part in Path(p).parts))
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(command: list[str], log_file: Path, env: dict) -> None:
    print("\n> " + subprocess.list2cmdline(command), flush=True)
    with log_file.open("a", encoding="utf-8", buffering=1) as log:
        log.write("\n> " + subprocess.list2cmdline(command) + "\n")
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", bufsize=1)
        try:
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
            code = process.wait()
        finally:
            if process.poll() is None:
                # Do not leave PyInstaller/smoke-test descendants after Ctrl+C or log failure.
                if os.name == "nt":
                    try:
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                    except (OSError, subprocess.TimeoutExpired):
                        process.kill()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            process.stdout.close()
        if code:
            raise RuntimeError(f"Step failed (exit {code}); see {log_file}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", default=datetime.now().strftime("%Y-%m-%d-%H%M%S"))
    ap.add_argument("--config", type=Path, default=ROOT / "build/update_config.json")
    ap.add_argument("--bundle", type=Path, help="Package this already built bundle instead of rebuilding")
    ap.add_argument("--skip-tests", action="store_true", help="Only for a bundle already tested separately")
    args = ap.parse_args()
    if not args.version or any(c not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-" for c in args.version):
        raise ValueError("Version must contain only letters, numbers, dots and hyphens")
    cfg = json.loads(args.config.read_text(encoding="utf-8-sig"))
    output = (ROOT / cfg.get("output", "release")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = (ROOT / cfg["base"]).resolve()
    if not base.is_file():
        raise FileNotFoundError(f"Keep the original full customer ZIP on the build machine: {base}")
    history_path = output / "update-history.json"
    target = output / f"VisionGuard-GPU-Update-{args.version}.zip"
    log_file = output / f"update-build-{args.version}.log"
    env = build_environment()
    lock_file = ROOT / "build/update-build.lock"
    # Exclusive file handle is released by Windows even if the process crashes.
    lock = lock_file.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            if lock.read(1) == b"":
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        history = recover_publications(output, base, history_path)
        updates = list(dict.fromkeys([*(str((ROOT / p).resolve()) for p in cfg.get("updates", [])),
                                      *(str(Path(p).resolve()) for p in history)]))
        for path in updates:
            if not Path(path).is_file():
                raise FileNotFoundError(f"Previous update is needed to support those customers: {path}")
        if target.exists():
            raise FileExistsError(f"Version already published: {target.name}; choose another version")
        sources_before = source_hashes()
        versions = {name: importlib.metadata.version(name) for name in RUNTIME_PACKAGES}
        versions["python"] = sys.version.split()[0]
        runtime_lock = ROOT / "build/runtime-lock.json"
        if runtime_lock.exists():
            expected = json.loads(runtime_lock.read_text(encoding="utf-8"))
            drift = {k: {"expected": v, "installed": versions.get(k)} for k, v in expected.items()
                     if versions.get(k) != v}
            if drift:
                raise RuntimeError("Build dependencies changed; use the locked environment before making a small update:\n"
                                   + json.dumps(drift, indent=2))
        if not args.skip_tests:
            test_env = {**env, "QT_QPA_PLATFORM": "offscreen"}
            run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], log_file, test_env)
        bundle = args.bundle.resolve() if args.bundle else ROOT / "build/dist-update/VisionGuard"
        if args.bundle is None:
            run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(bundle.parent),
                 "--workpath", str(ROOT / "build/work-update"), str(ROOT / "build/visionguard.spec")], log_file, env)
        run([sys.executable, str(ROOT / "build/smoke_bundle.py"), "--bundle", str(bundle)], log_file, env)
        with tempfile.TemporaryDirectory(prefix=".pending-update-", dir=output) as folder:
            staged = Path(folder) / target.name
            command = [sys.executable, str(ROOT / "build/make_update.py"), "--base", str(base),
                       "--bundle", str(bundle), "--version", args.version, "--output", folder,
                       "--max-size-mb", str(cfg.get("max_size_mb", 128))]
            for previous in updates:
                command.extend(["--also-base", previous])
            notes = ROOT / cfg.get("notes", "docs/RELEASE_NOTES.md")
            if notes.is_file():
                command.extend(["--notes", str(notes)])
            run(command, log_file, env)
            manifest = json.loads(staged.with_suffix(".manifest.json").read_text(encoding="utf-8"))
            # Only verified output is moved into the customer release folder.
            run([sys.executable, str(ROOT / "build/verify_update.py"), "--base", str(base),
                 "--update", str(staged), *[arg for p in updates for arg in ("--previous", p)]], log_file, env)
            if source_hashes() != sources_before:
                raise RuntimeError("Source files changed while building; no update was published. Run the build again.")
            publish_verified(staged, target, base, history_path, history)
        atomic_json(output / f"update-build-{args.version}.json", {
            "version": args.version, "runtime": versions, "baseline": str(base), "previous_updates": updates,
            "bundle": str(bundle), "package": str(target), "bytes": target.stat().st_size,
            "changed_files": len(manifest["files"]), "reused_files": len(manifest["required"]),
            "source_sha256": sources_before,
            "source_snapshot_kind": "prebuilt_bundle_inputs_unverified" if args.bundle else "built_from_snapshot",
        })
        print(f"\nTAO UPDATE THANH CONG: {target}\nSize: {target.stat().st_size / 1024**2:.2f} MiB", flush=True)
        print("Chi gui ZIP Update nay cho khach. Khach giai nen va chay Update.bat.")
        print("Giu ZIP ban day du va cac ZIP update cu tren may build cho nhung lan sau.")
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"TAO UPDATE THAT BAI: {exc}", file=sys.stderr)
        raise SystemExit(1)
