"""The backend initializes its own CPU thread defaults on its first prediction."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from visionguard.config.schemas import DetectorConfig
from visionguard.vision.yolo_detector import YoloDetector


class DetectorThreadTests(unittest.TestCase):
    def test_configured_cap_survives_predictor_initialization(self):
        count = [8]
        fake_torch = SimpleNamespace(get_num_threads=lambda: count[0],
                                     set_num_threads=lambda n: count.__setitem__(0, n))

        class Model:
            names = {0: "person"}

            def __init__(self, path):
                self.initialized = False

            def to(self, device):
                return self

            def predict(self, **kwargs):
                if not self.initialized:
                    count[0] = 7  # what Ultralytics' CPU device setup currently does
                    self.initialized = True
                return []

        with patch.dict(sys.modules, {"torch": fake_torch, "ultralytics": SimpleNamespace(YOLO=Model)}), \
                patch("visionguard.vision.yolo_detector.resolve_device", return_value="cpu"), \
                patch("visionguard.vision.yolo_detector.resolve_model_path", return_value=Path("fixture.pt")):
            detector = YoloDetector()
            detector.load(DetectorConfig(cpu_threads=2, imgsz=64))
            self.assertEqual(count[0], 2)
            import numpy as np
            detector.detect(np.zeros((64, 64, 3), dtype=np.uint8))
            self.assertEqual(count[0], 2)
            detector.unload()


if __name__ == "__main__":
    unittest.main()
