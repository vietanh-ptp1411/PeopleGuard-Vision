"""Exercise the customer updater on a fake installation, never live application files."""
import ctypes
import hashlib
import json
import os
import random
import sys
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zlib


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows updater")
class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vg_update_test_")
        self.root = Path(self.temp.name)
        self.install = self.root / "Client installation"
        self.package = self.root / "Update package"
        self.install.mkdir()
        self.package.mkdir()
        shutil.copy2(Path(__file__).resolve().parents[1] / "build/update.ps1", self.package / "update.ps1")
        self.before = {"VisionGuard.exe": b"old app", "VisionGuardLauncher.exe": b"old launcher"}
        self.after = {"VisionGuard.exe": b"new app", "VisionGuardLauncher.exe": b"new launcher"}
        files = []
        for relative in self.before:
            self.put(self.install / relative, self.before[relative])
            self.put(self.package / "payload" / relative, self.after[relative])
            files.append(dict(path=relative, sha256=self.hash(self.after[relative]),
                              base_sha256=self.hash(self.before[relative]), program=True))
        self.put(self.install / "_internal/runtime.dll", b"same runtime")
        self.sensitive = {"config/roi_config.json": b'{"client_roi":true}', "models/client.pt": b"model",
                          "events/events.db": b"events", "logs/client.log": b"log"}
        for relative, content in self.sensitive.items():
            self.put(self.install / relative, content)
        self.manifest = dict(format=1, version="test", files=files,
                             required=[dict(path="_internal/runtime.dll", sha256=self.hash(b"same runtime"))])
        self.write_manifest()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def put(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    @staticmethod
    def hash(data):
        return hashlib.sha256(data).hexdigest()

    def write_manifest(self):
        (self.package / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def invoke(self, *args):
        return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(self.package / "update.ps1"), "-InstallDir", str(self.install), *args],
                              capture_output=True, text=True, timeout=45)

    def assert_original(self):
        for relative, content in {**self.before, **self.sensitive}.items():
            self.assertEqual((self.install / relative).read_bytes(), content)

    def test_apply_preserves_site_data_and_backs_up_originals(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for relative, content in {**self.after, **self.sensitive}.items():
            self.assertEqual((self.install / relative).read_bytes(), content)
        backup = next((self.install / "_updates").iterdir())
        for relative, content in self.before.items():
            self.assertEqual((backup / "files" / relative).read_bytes(), content)
        self.assertEqual((backup / "config/roi_config.json").read_bytes(), self.sensitive["config/roi_config.json"])
        repeated = self.invoke()
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)

    def test_verify_only_changes_nothing(self):
        result = self.invoke("-VerifyOnly")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_original()
        self.assertFalse((self.install / "_updates").exists())

    def test_corrupt_payload_rejected_before_any_changes(self):
        self.put(self.package / "payload/VisionGuardLauncher.exe", b"truncated")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assert_original()
        self.assertFalse((self.install / "_updates").exists())

    def test_incompatible_runtime_rejected(self):
        self.put(self.install / "_internal/runtime.dll", b"different runtime")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assert_original()
        self.assertFalse((self.install / "_updates").exists())

    def test_manifest_cannot_replace_customer_configuration(self):
        self.manifest["files"].append(dict(path="config/roi_config.json", sha256="bad", program=False))
        self.write_manifest()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assert_original()

    def test_manifest_rejects_windows_path_aliases(self):
        for alias in ("_internal/./runtime.dll", "_internal/runtime.dll.", "_internal/runtime.dll ",
                      "_internal/../config/roi_config.json"):
            with self.subTest(path=alias):
                self.manifest["files"].append(dict(path=alias, sha256="bad", program=False))
                self.write_manifest()
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid package path", result.stdout + result.stderr)
                self.assert_original()
                self.assertFalse((self.install / "_updates").exists())
                self.manifest["files"].pop()

    def test_manifest_rejects_case_insensitive_duplicate(self):
        self.manifest["files"].append(dict(path="_internal/RUNTIME.dll", sha256="bad", program=False))
        self.write_manifest()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate package path", result.stdout + result.stderr)
        self.assert_original()

    def test_locked_second_file_rolls_back_first_file(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                       ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        # Permit reads (hash/backup), deny write/delete so the second replacement fails.
        handle = kernel.CreateFileW(str(self.install / "VisionGuardLauncher.exe"), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            result = self.invoke()
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((self.install / "_updates").exists(), result.stdout + result.stderr)
            self.assert_original()
        finally:
            kernel.CloseHandle(handle)


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows updater")
class DeltaUpdateTests(UpdateTests):
    """Format 2: executables rebuilt from the installed copy, for either installed version."""

    def setUp(self):
        super().setUp()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
        from vgdelta import make_delta
        rng = random.Random(4)
        modules = [zlib.compress(bytes(rng.randrange(256) for _ in range(3000))) for _ in range(60)]
        self.v1 = b"MZ bootloader" + b"".join(modules)
        self.v2 = self.v1.replace(modules[10], zlib.compress(b"update 02/10" * 300))   # a later update
        self.new = self.v1.replace(modules[30], zlib.compress(b"per-ROI bits" * 400)) + b"new toc"
        self.put(self.install / "VisionGuard.exe", self.v1)
        self.before["VisionGuard.exe"] = self.v1
        self.after["VisionGuard.exe"] = self.new
        (self.package / "payload/VisionGuard.exe").unlink()      # only deltas are shipped
        deltas = []
        for base in (self.v1, self.v2):
            patch = make_delta(base, self.new)
            self.assertLess(len(patch), len(self.new) // 4)
            name = f"deltas/VisionGuard.exe.{self.hash(base)[:12]}.vgdelta"
            self.put(self.package / name, patch)
            deltas.append(dict(base_sha256=self.hash(base), patch=name, sha256=self.hash(patch)))
        entry = self.manifest["files"][0]
        entry.update(sha256=self.hash(self.new), base_sha256=self.hash(self.v1), full=False,
                     accepted_sha256=[self.hash(self.v1), self.hash(self.v2)], deltas=deltas)
        self.manifest["format"] = 2
        self.write_manifest()

    def test_site_with_the_later_update_is_patched_too(self):
        self.put(self.install / "VisionGuard.exe", self.v2)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.install / "VisionGuard.exe").read_bytes(), self.new)

    def test_damaged_delta_rejected_before_any_changes(self):
        name = self.manifest["files"][0]["deltas"][0]["patch"]
        patch = bytearray((self.package / name).read_bytes())
        patch[-1] ^= 0xFF
        self.put(self.package / name, bytes(patch))
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assert_original()
        self.assertFalse((self.install / "_updates").exists())

    def test_wrong_delta_output_rejected_before_any_changes(self):
        entry = self.manifest["files"][0]
        entry["sha256"] = self.hash(b"something else")
        self.write_manifest()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assert_original()
        self.assertFalse((self.install / "_updates").exists())


if __name__ == "__main__":
    unittest.main()
