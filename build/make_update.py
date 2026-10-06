"""Package changed binaries against an existing full ZIP, without site data.

Build the new bundle using visionguard.spec first, then:
    python build/make_update.py --base release/VisionGuard-GPU.zip \
        --also-base release/VisionGuard-GPU-Update-2026-10-02.zip \
        --bundle path/to/new/VisionGuard --version 2026-10-04

--also-base names update ZIPs a site may already have applied on top of --base, in order.
A changed file is shipped as a binary delta (see vgdelta.py) from every such installed
version, and as a whole file only when no delta is possible: the two 64 MB executables
become a few hundred kB each.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import tempfile
from pathlib import Path
import time
from typing import Dict, List, Optional, Tuple
import zipfile

from vgdelta import apply_delta, make_delta

ROOT = Path(__file__).resolve().parent.parent

#: A delta must beat the whole file by this much, or the whole file is shipped instead.
DELTA_WORTH = 0.5

Ref = Tuple[zipfile.ZipFile, str] | Path


def validate_relative(relative: str) -> str:
    """Require one canonical Windows relative path; aliases cannot bypass the whitelist."""
    if not isinstance(relative, str) or not relative or any(c in relative for c in '\\<>:"|?*'):
        raise ValueError(f"Invalid package path: {relative!r}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10))}
    for part in relative.split("/"):
        if (not part or part in (".", "..") or part != part.rstrip(" .") or
                any(ord(c) < 32 for c in part) or part.split(".")[0].upper() in reserved):
            raise ValueError(f"Invalid package path: {relative!r}")
    return relative


def validate_manifest(manifest: dict) -> None:
    if manifest.get("format") not in (1, 2):
        raise ValueError("Unsupported update format")
    seen = set()
    for entry in [*manifest.get("required", []), *manifest["files"]]:
        relative = validate_relative(entry["path"])
        if not program_file(relative) and relative not in ("Install.bat", "BAT-DAU.md", "HUONG-DAN.md", "RELEASE-NOTES.txt"):
            raise ValueError(f"Package cannot modify this path: {relative}")
        if relative.casefold() in seen:
            raise ValueError(f"Duplicate package path: {relative}")
        seen.add(relative.casefold())
        for delta in entry.get("deltas", []):
            patch = validate_relative(delta["patch"])
            if not patch.startswith("deltas/"):
                raise ValueError(f"Invalid delta path: {patch}")


def open_ref(ref: Ref):
    return ref.open("rb") if isinstance(ref, Path) else ref[0].open(ref[1])


def read_ref(ref: Ref) -> bytes:
    with open_ref(ref) as stream:
        return stream.read()


def ref_hash(ref: Ref, hashes: dict) -> str:
    if ref not in hashes:
        with open_ref(ref) as stream:
            hashes[ref] = digest(stream)
    return hashes[ref]


def digest(stream) -> str:
    return hashlib.file_digest(stream, "sha256").hexdigest()


def program_file(relative: str) -> bool:
    validate_relative(relative)
    return relative.startswith("_internal/") or relative in ("VisionGuard.exe", "VisionGuardLauncher.exe")


def installed_states(stack: ExitStack, base: Path, updates: List[Path], hashes=None) -> List[Dict[str, Ref]]:
    """Program files of every installation the update must accept: the full ZIP, then each update applied on top."""
    baseline = stack.enter_context(zipfile.ZipFile(base))
    state = {name.removeprefix("VisionGuard/"): (baseline, name) for name in baseline.namelist()
             if name.startswith("VisionGuard/") and not name.endswith("/")
             and program_file(name.removeprefix("VisionGuard/"))}
    states = [state]
    hashes = {} if hashes is None else hashes
    staging = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="vg_update_bases_")))
    for path in updates:
        archive = stack.enter_context(zipfile.ZipFile(path))
        manifests = [n for n in archive.namelist() if n.count("/") == 1 and n.endswith("/manifest.json")]
        if len(manifests) != 1:
            raise SystemExit(f"{path.name} is not an update package")
        prefix = manifests[0].rsplit("/", 1)[0] + "/"
        manifest = json.loads(archive.read(manifests[0]))
        validate_manifest(manifest)
        for entry in manifest.get("required", []):
            ref = state.get(entry["path"])
            if ref is None or ref_hash(ref, hashes) != entry["sha256"]:
                raise ValueError(f"Previous update {path.name} needs a different baseline: {entry['path']}")
        overlay = {}
        for entry in manifest["files"]:
            relative = entry["path"]
            if not program_file(relative):
                continue
            current = state.get(relative)
            current_hash = ref_hash(current, hashes) if current is not None else None
            if current_hash == entry["sha256"]:
                continue
            accepted = {*entry.get("accepted_sha256", []), entry.get("base_sha256")}
            if current is not None and current_hash not in accepted:
                raise ValueError(f"Previous update {path.name} does not match {relative}")
            payload = prefix + "payload/" + relative
            if payload in archive.namelist():
                ref = (archive, payload)
                if ref_hash(ref, hashes) != entry["sha256"]:
                    raise ValueError(f"Corrupt previous update payload: {relative}")
                overlay[relative] = ref
                continue
            delta = next((d for d in entry.get("deltas", []) if d["base_sha256"] == current_hash), None)
            if current is None or delta is None:
                raise ValueError(f"Cannot reconstruct previous update {path.name}: {relative}")
            patch = archive.read(prefix + delta["patch"])
            if hashlib.sha256(patch).hexdigest() != delta["sha256"]:
                raise ValueError(f"Corrupt previous delta: {relative}")
            result = apply_delta(read_ref(current), patch)
            if hashlib.sha256(result).hexdigest() != entry["sha256"]:
                raise ValueError(f"Previous delta produced incorrect content: {relative}")
            ref = staging / hashlib.sha256(result).hexdigest()
            ref.write_bytes(result)
            hashes[ref] = entry["sha256"]
            overlay[relative] = ref
        state = {**state, **overlay}
        states.append(state)
    return states


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--also-base", type=Path, action="append", default=[],
                        help="update ZIP a site may already have applied on top of --base (repeat, in order)")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    parser.add_argument("--notes", type=Path, help="UTF-8 release notes included in the customer package")
    parser.add_argument("--max-size-mb", type=float, default=128.0,
                        help="Refuse unexpectedly large updates (default 128 MiB)")
    args = parser.parse_args()
    if not args.version or any(c not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-" for c in args.version):
        raise SystemExit("Use a version containing only letters, numbers, dots and hyphens")
    label = f"VisionGuard-GPU-Update-{args.version}"
    target = args.output / f"{label}.zip"
    partial = target.with_suffix(".zip.partial")
    if target.exists() or partial.exists():
        raise SystemExit("Output already exists; choose a new version")
    programs = {p.relative_to(args.bundle).as_posix(): p for p in args.bundle.rglob("*")
                if p.is_file() and program_file(p.relative_to(args.bundle).as_posix())}
    for exe in ("VisionGuard.exe", "VisionGuardLauncher.exe"):
        if exe not in programs:
            raise SystemExit(f"Missing new bundle file: {exe}")
    changed, required = [], []
    sources: Dict[str, Path] = {}          # package path -> file on disk
    blobs: Dict[str, bytes] = {}           # package path -> generated delta
    hashes: Dict[Ref, str] = {}
    last = time.monotonic()
    print("Comparing the new bundle with every installation it must update...", flush=True)
    with ExitStack() as stack:
        states = installed_states(stack, args.base, args.also_base, hashes)

        def old_hash(ref: Optional[Ref]) -> Optional[str]:
            if ref is None:
                return None
            if ref not in hashes:
                with open_ref(ref) as stream:
                    hashes[ref] = digest(stream)
            return hashes[ref]

        removed = set().union(*states) - set(programs)
        if removed:
            raise SystemExit(f"New bundle removes program files; review before making a patch: {sorted(removed)}")
        for index, (relative, path) in enumerate(sorted(programs.items()), 1):
            with path.open("rb") as stream:
                new_hash = digest(stream)
            size = path.stat().st_size
            bases = [(old_hash(state.get(relative)), state.get(relative)) for state in states]
            entry = {"path": relative, "sha256": new_hash, "size": size}
            if all(h == new_hash for h, _ in bases):
                required.append(entry)
            else:
                # Large runtime DLL changes should not allocate gigabytes for delta matching.
                new_bytes = path.read_bytes() if size <= 256 * 1024 ** 2 else None
                deltas, full = [], False
                for h, ref in dict(bases).items():               # one delta per distinct installed version
                    if h == new_hash:
                        continue
                    if ref is None:
                        full = True                              # file is new for this installation
                        continue
                    if new_bytes is None:
                        full = True
                        continue
                    old_bytes = read_ref(ref)
                    patch = make_delta(old_bytes, new_bytes)
                    if len(patch) > size * DELTA_WORTH or apply_delta(old_bytes, patch) != new_bytes:
                        full = True
                        continue
                    name = f"deltas/{relative}.{h[:12]}.vgdelta"
                    blobs[name] = patch
                    deltas.append({"base_sha256": h, "patch": name,
                                   "sha256": hashlib.sha256(patch).hexdigest(), "size": len(patch)})
                if full:
                    sources[f"payload/{relative}"] = path
                changed.append({**entry, "base_sha256": bases[0][0], "program": True, "full": full,
                                "accepted_sha256": sorted({h for h, _ in bases if h and h != new_hash}),
                                "deltas": deltas})
            if time.monotonic() - last > 15:
                print(f"  compared {index}/{len(programs)} files", flush=True)
                last = time.monotonic()
    for relative, path in (("Install.bat", ROOT / "build/Install.bat"),
                           ("BAT-DAU.md", ROOT / "build/BAT-DAU.md"),
                           ("HUONG-DAN.md", ROOT / "tools/README-TRIEN-KHAI.md")):
        changed.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "size": path.stat().st_size, "program": False, "full": True})
        sources[f"payload/{relative}"] = path
    manifest = {"format": 2, "version": args.version,
                "baseline": " + ".join(p.name for p in [args.base, *args.also_base]),
                "files": changed, "required": required}
    validate_manifest(manifest)
    release_notes = args.notes.read_text(encoding="utf-8-sig") if args.notes else "Cập nhật chương trình, giữ nguyên cấu hình và dữ liệu."
    instructions = fr"""VisionGuard GPU — cập nhật {args.version}

Gói này dành cho máy đã cài bản đầy đủ tương thích:
{args.base.name}
Các bản cập nhật trước được hỗ trợ:
{chr(10).join(p.name for p in args.also_base) or '(bản đầy đủ chưa cập nhật)'}

1. Dừng giám sát vào thời điểm phù hợp, đóng VisionGuard và đợi launcher thoát.
2. Giải nén ZIP update vào một thư mục riêng; không chép đè bằng tay.
3. Bấm Update.bat và nhập thư mục đang chứa VisionGuard.exe.
4. Chờ CAP NHAT THANH CONG, sau đó mở lại phần mềm và kiểm tra camera/ROI/PLC.

Updater kiểm tra SHA-256 trước khi thay file, sao lưu chương trình cũ vào _updates,
tự phục hồi các file đã thay nếu cập nhật thất bại. Không ghi đè config, models,
events hoặc logs. Không cần tải lại thư viện CUDA/model khi chúng không thay đổi.
Máy chưa cài VisionGuard cần gói cài đầy đủ, không dùng ZIP update để cài mới.
Nếu phiên bản không khớp, updater dừng trước khi thay đổi chương trình.

Thay đổi trong phiên bản này:
{release_notes}

Kiểm tra gói mà chưa cài: Update.bat -InstallDir "C:\VisionGuard" -VerifyOnly
Khôi phục thủ công: đóng ứng dụng, chép các file trong _updates/<lần cập nhật>/files
về vị trí tương ứng. Bản sao config trước cập nhật cũng nằm trong thư mục đó.
"""
    batch = "@echo off\r\ncd /d \"%~dp0\"\r\npowershell -NoProfile -ExecutionPolicy Bypass -File \"%~dp0update.ps1\" %*\r\nset \"VG_UPDATE_EXIT=%ERRORLEVEL%\"\r\npause\r\nexit /b %VG_UPDATE_EXIT%\r\n"
    args.output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, source in sources.items():
            archive.write(source, f"{label}/{name}")
        for name, blob in blobs.items():
            archive.writestr(f"{label}/{name}", blob)
        archive.writestr(f"{label}/manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        archive.write(ROOT / "build/update.ps1", f"{label}/update.ps1")
        archive.writestr(f"{label}/Update.bat", batch)
        archive.writestr(f"{label}/HUONG-DAN-CAP-NHAT.txt", instructions.encode("utf-8-sig"))
    with zipfile.ZipFile(partial) as archive:
        bad = archive.testzip()
        if bad:
            raise SystemExit(f"ZIP CRC failed: {bad}")
    if partial.stat().st_size > args.max_size_mb * 1024 ** 2:
        size_mb = partial.stat().st_size / 1024 ** 2
        partial.unlink()
        raise SystemExit(f"Update is {size_mb:.1f} MiB, exceeding {args.max_size_mb:g} MiB. "
                         "Check runtime dependency drift; no customer ZIP was published.")
    partial.rename(target)
    with target.open("rb") as stream:
        checksum = digest(stream)
    target.with_suffix(".zip.sha256").write_text(f"{checksum}  {target.name}\n", encoding="ascii")
    target.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Ready: {target} ({target.stat().st_size / 1e6:.1f} MB)", flush=True)
    print(f"Changed: {len(changed)} ({len(blobs)} deltas, {len(sources)} whole files); "
          f"reused: {len(required)} files; SHA256: {checksum}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
