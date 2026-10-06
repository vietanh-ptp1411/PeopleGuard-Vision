"""Apply a real update to disposable copies of every supported customer version.

Streams the original runtime once; resets changed files between installation states.
Never touches a real installed application or connects to hardware.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from make_update import installed_states, open_ref, ref_hash


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_destination(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError(f"Unsafe package path: {relative}")
    return path


def validate(base: Path, previous: list[Path], update: Path, work_parent: Path) -> dict:
    reports = []
    hashes = {}
    with ExitStack() as stack:
        states = installed_states(stack, base, previous, hashes)
        folder = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="customer-update-test-", dir=work_parent)))
        install = folder / "Customer installation"
        package = folder / "Package"
        install.mkdir()
        package.mkdir()
        with zipfile.ZipFile(update) as archive:
            for item in archive.infolist():
                target = safe_destination(package, item.filename)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as source, target.open("wb") as dest:
                        shutil.copyfileobj(source, dest, 1024 ** 2)
        scripts = list(package.glob("*/update.ps1"))
        if len(scripts) != 1:
            raise ValueError("Missing customer updater")
        script = scripts[0]
        manifest = json.loads((script.parent / "manifest.json").read_text(encoding="utf-8"))
        expected = {e["path"]: e["sha256"] for e in manifest["files"] + manifest["required"]}
        current = {}
        protected = {"config/roi_config.json": b'{"site":"kept","roi":true}',
                     "models/site.pt": b"customer model sentinel", "events/events.db": b"customer events sentinel",
                     "logs/customer.log": b"customer log sentinel"}
        for name, data in protected.items():
            p = safe_destination(install, name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        for index, state in enumerate(states):
            print(f"Preparing customer version {index + 1}/{len(states)}...", flush=True)
            for relative, ref in state.items():
                wanted = ref_hash(ref, hashes)
                if current.get(relative) == wanted:
                    continue
                target = safe_destination(install, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with open_ref(ref) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest, 1024 ** 2)
                current[relative] = wanted
            # New files added by the candidate must be absent in an older installation.
            for relative in set(current) - set(state):
                safe_destination(install, relative).unlink(missing_ok=True)
                current.pop(relative, None)
            command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                       "-InstallDir", str(install)]
            for repeat in range(2):
                result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                        errors="replace", timeout=240)
                if result.returncode:
                    raise RuntimeError(f"Customer version {index + 1} update failed:\n{result.stdout}\n{result.stderr}")
                for relative, wanted in expected.items():
                    actual = digest(safe_destination(install, relative))
                    if actual != wanted:
                        raise RuntimeError(f"Installed bytes differ: {relative}")
                for relative, data in protected.items():
                    if safe_destination(install, relative).read_bytes() != data:
                        raise RuntimeError(f"Customer data changed: {relative}")
                print(f"  Applied {'again' if repeat else 'once'}: {len(expected)} file hashes match; site data unchanged", flush=True)
            current = dict(expected)
            reports.append({"source": base.name if index == 0 else previous[index - 1].name,
                            "installed_files_verified": len(expected), "repeat_apply": True,
                            "customer_data_unchanged": True})
        return {"update": update.name, "installations": reports}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--previous", type=Path, action="append", default=[])
    ap.add_argument("--update", type=Path, required=True)
    args = ap.parse_args()
    result = validate(args.base, args.previous, args.update, args.update.resolve().parent)
    output = args.update.with_suffix(".customer-validation.json")
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("CUSTOMER UPDATE TEST PASSED: " + str(output), flush=True)


if __name__ == "__main__":
    main()
