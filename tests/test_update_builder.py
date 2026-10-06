"""Build successive small delta releases, including a previous delta-only update."""
from contextlib import ExitStack
from pathlib import Path
import json
import random
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib

BUILD = Path(__file__).resolve().parents[1] / "build"
sys.path.insert(0, str(BUILD))
import build_update
from make_update import installed_states, read_ref, validate_manifest, validate_relative
from verify_update import verify
from vgdelta import apply_delta


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vg_builder_")
        self.root = Path(self.temp.name)
        self.base = self.root / "full.zip"
        self.bundle = self.root / "bundle"
        self.output = self.root / "releases"
        (self.bundle / "_internal").mkdir(parents=True)
        rng = random.Random(8)
        self.parts = [zlib.compress(rng.randbytes(3000)) for _ in range(30)]
        self.v1 = b"MZ" + b"".join(self.parts)
        self.v2 = self.v1.replace(self.parts[3], zlib.compress(b"change one" * 700))
        self.v3 = self.v2.replace(self.parts[20], zlib.compress(b"change two" * 700))
        self.files = {"VisionGuard.exe": self.v1, "VisionGuardLauncher.exe": self.v1,
                      "_internal/runtime.dll": b"runtime" * 400}
        with zipfile.ZipFile(self.base, "w") as z:
            for name, data in self.files.items():
                z.writestr("VisionGuard/" + name, data)
                (self.bundle / name).write_bytes(data)
            z.writestr("VisionGuard/config/roi_config.json", b'{"private":true}')

    def tearDown(self):
        self.temp.cleanup()

    def build(self, version, previous=()):
        command = [sys.executable, str(BUILD / "make_update.py"), "--base", str(self.base),
                   "--bundle", str(self.bundle), "--version", version, "--output", str(self.output)]
        for path in previous:
            command.extend(["--also-base", str(path)])
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.output / f"VisionGuard-GPU-Update-{version}.zip"

    def test_two_generations_accept_full_and_all_previous_updates(self):
        (self.bundle / "VisionGuard.exe").write_bytes(self.v2)
        first = self.build("first")
        with zipfile.ZipFile(first) as z:
            self.assertFalse(any(n.endswith("payload/VisionGuard.exe") for n in z.namelist()))
            self.assertFalse(any("config/" in n or "models/" in n for n in z.namelist()))
        (self.bundle / "VisionGuard.exe").write_bytes(self.v3)
        second = self.build("second", [first])
        report = verify(self.base, [first], second)
        self.assertEqual(report["supported_installations"], 2)
        with ExitStack() as stack:
            states = installed_states(stack, self.base, [first, second])
            self.assertEqual(read_ref(states[0]["VisionGuard.exe"]), self.v1)
            self.assertEqual(read_ref(states[1]["VisionGuard.exe"]), self.v2)
            self.assertEqual(read_ref(states[2]["VisionGuard.exe"]), self.v3)

    def test_corrupt_previous_delta_is_rejected(self):
        (self.bundle / "VisionGuard.exe").write_bytes(self.v2)
        first = self.build("first")
        damaged = self.root / "damaged.zip"
        with zipfile.ZipFile(first) as src, zipfile.ZipFile(damaged, "w") as dst:
            for info in src.infolist():
                data = src.read(info)
                if info.filename.endswith(".vgdelta"):
                    data = data[:-1] + bytes([data[-1] ^ 0xff])
                dst.writestr(info, data)
        with ExitStack() as stack, self.assertRaisesRegex(ValueError, "Corrupt previous delta"):
            installed_states(stack, self.base, [damaged])

    def test_size_guard_does_not_publish_large_zip(self):
        (self.bundle / "VisionGuard.exe").write_bytes(random.Random(9).randbytes(100000))
        result = subprocess.run([sys.executable, str(BUILD / "make_update.py"), "--base", str(self.base),
                                 "--bundle", str(self.bundle), "--version", "large", "--output", str(self.output),
                                 "--max-size-mb", "0.001"], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list(self.output.glob("*.zip")))
        self.assertFalse(list(self.output.glob("*.partial")))

    def test_truncated_delta_literal_is_rejected(self):
        with self.assertRaises(ValueError):
            apply_delta(b"", b"VGDELTA1\x02\x05\x00\x00\x00abc")

    def test_windows_path_aliases_and_traversal_are_rejected(self):
        for path in ("_internal/./runtime.dll", "_internal/runtime.dll.", "_internal/runtime.dll ",
                     "_internal/../config/app.json", "_internal\\runtime.dll", "_internal/CON.txt",
                     "_internal//runtime.dll", "C:/VisionGuard.exe"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "Invalid package path"):
                validate_relative(path)
        with self.assertRaisesRegex(ValueError, "Duplicate package path"):
            validate_manifest(dict(format=2, required=[dict(path="_internal/runtime.dll")],
                                   files=[dict(path="_internal/RUNTIME.dll")]))


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vg_publish_")
        self.root = Path(self.temp.name)
        self.base = self.root / "full.zip"
        self.base.write_bytes(b"baseline")
        self.output = self.root / "release"
        self.output.mkdir()
        stage = self.root / "pending"
        stage.mkdir()
        self.staged = stage / "VisionGuard-GPU-Update-test.zip"
        self.staged.write_bytes(b"already verified package")
        for suffix in (".zip.sha256", ".manifest.json", ".validation.json"):
            self.staged.with_suffix(suffix).write_text("{}", encoding="utf-8")
        self.target = self.output / self.staged.name
        self.history = self.output / "update-history.json"

    def tearDown(self):
        self.temp.cleanup()

    def interrupted_after_zip(self):
        write = build_update.atomic_json

        def fail_history(path, data):
            if path == self.history:
                raise OSError("simulated interruption after ZIP rename")
            write(path, data)

        with mock.patch.object(build_update, "atomic_json", side_effect=fail_history):
            with self.assertRaises(OSError):
                build_update.publish_verified(self.staged, self.target, self.base, self.history, [])

    def test_interrupted_verified_publication_rejoins_history(self):
        self.interrupted_after_zip()
        self.assertTrue(self.target.exists())
        recovered = build_update.recover_publications(self.output, self.base, self.history)
        self.assertEqual(recovered, [str(self.target)])
        self.assertEqual(json.loads(self.history.read_text()), recovered)
        self.assertFalse(list(self.output.glob(".update-publish-*.json")))
        self.assertEqual(build_update.recover_publications(self.output, self.base, self.history), recovered)

    def test_failure_before_zip_does_not_register_unpublished_or_unrelated_zip(self):
        self.staged.with_suffix(".validation.json").unlink()
        unrelated = self.output / "VisionGuard-GPU-Update-unverified.zip"
        unrelated.write_bytes(b"not verified")
        with self.assertRaises(OSError):
            build_update.publish_verified(self.staged, self.target, self.base, self.history, [])
        self.assertFalse(self.target.exists())
        self.assertEqual(build_update.recover_publications(self.output, self.base, self.history), [])
        self.assertFalse(self.history.exists())

    def test_corrupt_interrupted_publication_stays_unregistered(self):
        self.interrupted_after_zip()
        self.target.write_bytes(b"changed after verification")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            build_update.recover_publications(self.output, self.base, self.history)
        self.assertFalse(self.history.exists())


if __name__ == "__main__":
    unittest.main()
