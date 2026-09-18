"""Check everything VisionGuard needs before it starts, and say plainly what is missing.

Run on its own to diagnose a machine, or by the launcher, which starts the application only
when nothing here is fatal.

    python tools/preflight.py            # human readable report
    python tools/preflight.py --json     # machine readable, for a service wrapper
    python tools/preflight.py --quiet    # exit code only

Exit codes:  0 = ready to run    1 = something fatal    2 = ready, with warnings
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import sys
from pathlib import Path
from typing import List, Tuple

from ..utils.paths import app_dir

PROJECT = app_dir()

OK, WARN, FAIL = "OK", "WARN", "FAIL"
MIN_FREE_GB = 5.0


class Report:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str]] = []

    def add(self, status: str, check: str, detail: str = "") -> None:
        self.rows.append((status, check, detail))

    @property
    def failed(self) -> bool:
        return any(s == FAIL for s, _, _ in self.rows)

    @property
    def warned(self) -> bool:
        return any(s == WARN for s, _, _ in self.rows)


def check_python(r: Report) -> None:
    v = sys.version_info
    text = f"{v.major}.{v.minor}.{v.micro}"
    r.add(OK if v >= (3, 10) else FAIL, "Python", f"{text} ({sys.executable})")


def check_packages(r: Report) -> None:
    import importlib
    for name, label in (("PySide6", "PySide6"), ("cv2", "opencv-python"),
                        ("numpy", "numpy"), ("requests", "requests")):
        try:
            importlib.import_module(name)
            r.add(OK, label, "installed")
        except Exception as exc:
            r.add(FAIL, label, f"missing ({exc})")
    try:
        import torch
        cuda = torch.cuda.is_available()
        gpu = torch.cuda.get_device_name(0) if cuda else "CPU only"
        r.add(OK, "torch", f"{torch.__version__} - {gpu}")
    except Exception as exc:
        r.add(WARN, "torch", f"not installed ({exc}) - AI Camera mode still works")
    try:
        import ultralytics
        r.add(OK, "ultralytics", ultralytics.__version__)
    except Exception as exc:
        r.add(WARN, "ultralytics", f"not installed ({exc}) - AI Camera mode still works")


CONFIG_DIR = "config"


def load_settings():
    from visionguard.config.config_manager import ConfigManager
    return ConfigManager(CONFIG_DIR).load_all()


def check_config(r: Report):
    for name in ("app_config.json", "camera_config.json", "plc_config.json", "ai_config.json"):
        path = Path(CONFIG_DIR) / name
        if not path.exists():
            r.add(WARN, f"config/{name}", "missing - defaults will be written")
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
            r.add(OK, f"config/{name}", "valid")
        except Exception as exc:
            r.add(FAIL, f"config/{name}", f"not valid JSON ({exc})")
    try:
        settings = load_settings()
        r.add(OK, "Config loads", f"{settings.camera.camera_count} camera(s), "
                                  f"{settings.camera.mode_enum.label}")
        return settings
    except Exception as exc:
        r.add(FAIL, "Config loads", str(exc))
        return None


def check_model(r: Report, settings) -> None:
    if settings is None or settings.camera.is_ai_camera:
        return                                   # the camera detects; no model needed
    path = Path(settings.ai.detector.model_path)
    if path.exists():
        r.add(OK, "YOLO model", f"{path} ({path.stat().st_size / 1e6:.1f} MB)")
    elif settings.ai.detector.auto_download:
        r.add(WARN, "YOLO model", f"{path} missing - will be downloaded (needs internet)")
    else:
        r.add(FAIL, "YOLO model", f"{path} missing and auto-download is off")


def _reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def check_cameras(r: Report, settings) -> None:
    if settings is None:
        return
    cam = settings.camera
    for i in range(cam.camera_count):
        unit = cam.unit(i)
        label = cam.label(i)
        if unit.is_ai_camera:
            host, port = unit.ai_camera.ip, unit.ai_camera.http_port
        elif unit.type_enum.value == "rtsp":
            host, port = unit.rtsp.ip, unit.rtsp.port
        elif unit.type_enum.value == "video":
            # An empty path becomes Path("."), and "." exists - so a camera with nothing
            # configured used to pass this check and then fail at the first frame. The
            # whole point of a pre-flight is that it fails here instead of at the site.
            raw = (unit.video.path or "").strip()
            path = Path(raw)
            if not raw:
                r.add(FAIL, label, "no video file configured")
            elif not path.is_file():
                what = "is a folder, not a video" if path.is_dir() else "not found"
                r.add(FAIL, label, f"video file {path} - {what}")
            else:
                r.add(OK, label, f"video file {path.name} ({path.stat().st_size / 1e6:.0f} MB)")
            continue
        else:
            r.add(OK, label, unit.describe_source())
            continue
        if not host:
            r.add(FAIL, label, "no IP configured")
        elif _reachable(host, port):
            r.add(OK, label, f"{host}:{port} answers")
        else:
            r.add(FAIL, label, f"{host}:{port} does not answer - check IP, cable, firewall")


def check_plc(r: Report, settings) -> None:
    if settings is None:
        return
    plc = settings.plc
    if plc.simulation_mode:
        r.add(WARN, "PLC", "SIMULATION - nothing is written to real hardware")
        return
    host, port = plc.connection.ip, plc.connection.port
    if not str(host).strip():
        # Without this the report says "does not answer", which sends somebody looking
        # for a cable fault when the real answer is that nobody has typed the address.
        r.add(FAIL, "PLC", "no IP configured - enter the PLC address, or tick Simulation")
    elif _reachable(host, port):
        r.add(OK, "PLC", f"{host}:{port} answers (MC Protocol {plc.connection.frame})")
    else:
        r.add(FAIL, "PLC", f"{host}:{port} does not answer - is the SLMP connection declared "
                           f"in GX Works and the PLC powered?")


def check_disk(r: Report, settings) -> None:
    try:
        free_gb = shutil.disk_usage(PROJECT).free / 1e9
    except OSError as exc:
        r.add(WARN, "Disk", str(exc))
        return
    status = OK if free_gb >= MIN_FREE_GB else FAIL
    detail = f"{free_gb:.1f} GB free"
    if settings is not None and settings.app.clip.enabled:
        detail += f"; clips capped at {settings.app.clip.max_total_gb:.0f} GB"
    r.add(status, "Disk space", detail)


def check_writable(r: Report) -> None:
    for folder in ("logs", "events", "config"):
        path = PROJECT / folder
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            r.add(OK, f"{folder}/ writable", str(path))
        except OSError as exc:
            r.add(FAIL, f"{folder}/ writable", str(exc))


def run() -> Report:
    r = Report()
    check_python(r)
    check_packages(r)
    check_writable(r)
    settings = check_config(r)
    check_model(r, settings)
    check_cameras(r, settings)
    check_plc(r, settings)
    check_disk(r, settings)
    return r


def report_text(r: "Report") -> str:
    width = max(len(c) for _, c, _ in r.rows)
    lines = ["=" * (width + 40), "VisionGuard - kiem tra truoc khi chay", "=" * (width + 40)]
    for status, check, detail in r.rows:
        mark = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}[status]
        lines.append(f"{mark}  {check.ljust(width)}  {detail}")
    lines.append("-" * (width + 40))
    if r.failed:
        lines.append("KHONG THE CHAY: sua cac muc [FAIL] o tren roi thu lai.")
    elif r.warned:
        lines.append("CHAY DUOC, nhung co canh bao [WARN] o tren.")
    else:
        lines.append("SAN SANG: moi thu deu dat.")
    return "\n".join(lines)


def main(config_dir: str = "config", as_json: bool = False, quiet: bool = False) -> int:
    class _A:
        pass

    args = _A()
    args.json, args.quiet = as_json, quiet
    if __name__ == "__main__":
        ap = argparse.ArgumentParser(description="VisionGuard pre-flight check")
        ap.add_argument("--json", action="store_true")
        ap.add_argument("--quiet", action="store_true")
        ap.add_argument("--config-dir", default=config_dir)
        args = ap.parse_args()
        config_dir = args.config_dir

    global CONFIG_DIR
    CONFIG_DIR = config_dir
    r = run()
    if sys.stdout is None:
        # Frozen windowed build: there is nowhere to print, so leave the report on disk.
        try:
            out = PROJECT / "logs"
            out.mkdir(parents=True, exist_ok=True)
            (out / "preflight.txt").write_text(report_text(r), encoding="utf-8")
        except OSError:
            pass
        return 1 if r.failed else (2 if r.warned else 0)
    if args.json:
        print(json.dumps([{"status": s, "check": c, "detail": d} for s, c, d in r.rows], indent=2))
    elif not args.quiet:
        print(report_text(r))
    return 1 if r.failed else (2 if r.warned else 0)


