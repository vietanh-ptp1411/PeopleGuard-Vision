"""Repeatable synthetic UI/pipeline/storage benchmark; never opens a camera or real PLC.

This measures application overhead. It is not a YOLO/decode or hardware acceptance test.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--cameras", type=int, choices=range(1, 7), default=4)
    ap.add_argument("--media", action="store_true")
    ap.add_argument("--maximize-one", action="store_true")
    ap.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(args.source_root))
    import numpy as np
    import psutil
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from visionguard.app.main_window import MainWindow
    from visionguard.camera.base_camera import Frame
    from visionguard.config.config_manager import ConfigManager
    from visionguard.vision.detection import BBox, Detection
    from visionguard.workers.camera_worker import CameraState

    class SyntheticDetector:
        def is_loaded(self): return True
        def unload(self): pass
        def reset_tracker(self): pass
        def detect(self, image):
            if int(time.monotonic() - start) % 6 < 3:
                return [Detection(BBox(300, 100, 450, 650), .95, 0, "person")]
            return []

    app = QApplication.instance() or QApplication([])
    process = psutil.Process()
    samples, delays = [], []
    with tempfile.TemporaryDirectory(prefix="vg_benchmark_") as folder:
        cm = ConfigManager(Path(folder) / "config")
        s = cm.settings
        s.camera.detection_mode = "pc_yolo"
        s.camera.camera_type = "video"
        s.camera.count = args.cameras
        s.app.autostart = False
        s.ai.detector.auto_load_on_start = False
        s.ai.detector.max_fps = 8
        s.app.events_db_path = str(Path(folder) / "events.db")
        s.app.snapshot.directory = str(Path(folder) / "snapshots")
        s.app.clip.directory = str(Path(folder) / "clips")
        s.app.retention.log_dir = str(Path(folder) / "logs")
        s.app.snapshot.enabled = s.app.clip.enabled = args.media
        s.app.snapshot.jpeg_quality = 85
        s.app.clip.fps = 15
        s.plc.simulation_mode = True
        cm.save_all()
        window = MainWindow(cm)
        c = window.ctrl
        for index in range(args.cameras):
            c.add_roi("include", [(0., 0.), (1., 0.), (1., 1.), (0., 1.)], camera=index)
            roi = c.roi_manager.all()[-1]
            c.update_roi_fields(roi.id, f"Camera {index + 1}", f"M{200 + index}", True, camera=index)
        c.inference_worker._detector = SyntheticDetector()
        c._model_loaded = True
        c._camera_state = CameraState.STREAMING
        c._camera_states = {i: CameraState.STREAMING for i in range(args.cameras)}
        images = [np.full((720, 1280, 3), 35 + index * 30, np.uint8) for index in range(args.cameras)]
        window.show()
        if args.maximize_one:
            window.video.set_maximized(0)
        start = time.monotonic()
        frame_id = [0]

        def feed():
            frame_id[0] += 1
            for index, image in enumerate(images):
                frame = Frame(image, frame_id[0], time.time(), "synthetic", index)
                c.buffers[index].put(frame)
                # New recorder path runs at capture, independently of UI preview.
                sink = getattr(c.camera_workers[index], "_frame_sink", None)
                if sink:
                    sink(image, time.monotonic())
                c._on_frame(frame)

        last_tick = [time.monotonic()]
        def tick():
            now = time.monotonic()
            if now - start > 5:
                delays.append(max(0.0, (now - last_tick[0] - .02) * 1000))
            last_tick[0] = now

        process.cpu_percent(None)
        def sample():
            now = time.monotonic()
            cpu = process.cpu_percent(None) / (psutil.cpu_count() or 1)
            if now - start > 5:
                samples.append({"seconds": now - start, "cpu_machine_pct": cpu,
                                "rss_mib": process.memory_info().rss / 1024 ** 2,
                                "threads": process.num_threads(),
                                "listeners": len(c.roi_manager._listeners),
                                "recorder_buffer_mib": sum(getattr(rec, "buffer_bytes", 0) for rec in c.recorders) / 1024 ** 2})

        feeder, jitter, meter = QTimer(), QTimer(), QTimer()
        feeder.timeout.connect(feed); feeder.start(50)
        jitter.timeout.connect(tick); jitter.start(20)
        meter.timeout.connect(sample); meter.start(1000)
        QTimer.singleShot(100, c.start_system)
        def finish():
            c.stop_system()  # close without the operator's interactive STOP confirmation
            app.quit()
        QTimer.singleShot(int(args.seconds * 1000), finish)
        app.exec()
        feeder.stop(); jitter.stop(); meter.stop()
        window.close()
        app.processEvents()
    ordered = sorted(delays)
    percentile = lambda p: ordered[min(len(ordered) - 1, int(len(ordered) * p))] if ordered else None
    report = {"kind": "synthetic; excludes YOLO and camera decode", "seconds": args.seconds,
              "cameras": args.cameras, "media": args.media, "maximize_one": args.maximize_one,
              "cpu_mean_machine_pct": statistics.mean(s["cpu_machine_pct"] for s in samples) if samples else None,
              "rss_peak_mib": max((s["rss_mib"] for s in samples), default=None),
              "ui_timer_lateness_p95_ms": percentile(.95), "ui_timer_lateness_p99_ms": percentile(.99),
              "samples": samples}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, indent=2))


if __name__ == "__main__":
    main()
