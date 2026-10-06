"""Start the frozen GUI using isolated config, no camera, no model and a simulated PLC."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", type=Path, required=True)
    args = ap.parse_args()
    executable = args.bundle.resolve() / "VisionGuard.exe"
    with tempfile.TemporaryDirectory(prefix="vg_frozen_smoke_") as folder:
        root = Path(folder)
        cfg = root / "config"
        cfg.mkdir()
        data = {
            "app_config.json": {"autostart": False, "events_db_path": str(root / "events.db"),
                                "snapshot": {"enabled": False, "directory": str(root / "snapshots")},
                                "clip": {"enabled": False, "directory": str(root / "clips")},
                                "retention": {"log_dir": str(root / "logs")}},
            "ai_config.json": {"detector": {"auto_load_on_start": False, "auto_download": False}},
            "camera_config.json": {"detection_mode": "pc_yolo", "camera_type": "video", "count": 1},
            "plc_config.json": {"simulation_mode": True},
        }
        for name, values in data.items():
            (cfg / name).write_text(json.dumps(values), encoding="utf-8")
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "VISIONGUARD_ENV_READY": "1",
               "OPENCV_FFMPEG_CAPTURE_OPTIONS": "rtsp_transport;tcp|analyzeduration;200000"}
        result = subprocess.run([str(executable), "--config-dir", str(cfg), "--smoke-test"],
                                cwd=root, env=env, timeout=90)
        if result.returncode:
            raise SystemExit(f"Frozen GUI smoke test failed: exit {result.returncode}")
        # The constructor's storage worker must have opened the isolated event database.
        if not (root / "events.db").exists():
            raise SystemExit("Frozen GUI did not initialize (another instance may be running)")
        marker = cfg / "smoke-result.json"
        if not marker.exists() or json.loads(marker.read_text(encoding="utf-8")) != {"graceful_exit": True, "exit_code": 0}:
            raise SystemExit("Frozen GUI did not confirm graceful shutdown")
    print("FROZEN GUI SMOKE TEST PASSED: isolated startup and graceful shutdown", flush=True)


if __name__ == "__main__":
    main()
