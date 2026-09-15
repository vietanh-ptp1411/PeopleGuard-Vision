"""Ultralytics YOLO person detector (pretrained, class 0 = person). Optional ByteTrack IDs."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import List, Optional

import numpy as np

from ..config.schemas import DetectorConfig
from .detection import BBox, Detection
from .detector import BaseDetector, DetectorError, DetectorInfo

log = logging.getLogger("AI")

MODELS_DIR = Path("models")
DEFAULT_MODEL_NAMES = ("yolo11n.pt", "yolov8n.pt")


def resolve_device(requested: str) -> str:
    """auto/cpu/cuda -> concrete torch device string, falling back to CPU."""
    req = (requested or "auto").strip().lower()
    try:
        import torch  # noqa: WPS433 (heavy import, only here)

        cuda_ok = torch.cuda.is_available()
    except Exception:
        cuda_ok = False
    if req in ("auto", ""):
        return "cuda:0" if cuda_ok else "cpu"
    if req.startswith("cuda") or req == "gpu":
        if cuda_ok:
            return "cuda:0" if req in ("cuda", "gpu") else req
        log.warning("CUDA requested but not available - using CPU")
        return "cpu"
    return "cpu"


def resolve_model_path(model_path: str, auto_download: bool) -> Path:
    """Find the model file: given path -> models/<name> -> any models/*.pt -> download."""
    candidates: List[Path] = []
    p = Path(model_path) if model_path else Path()
    if model_path:
        candidates.append(p)
        candidates.append(MODELS_DIR / p.name)
    for c in candidates:
        if c.is_file():
            return c
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    found = sorted(MODELS_DIR.glob("*.pt"))
    if found:
        log.warning("Model %s not found, using %s", model_path, found[0])
        return found[0]
    name = p.name if p.suffix == ".pt" else DEFAULT_MODEL_NAMES[0]
    if not auto_download:
        raise DetectorError(f"Model not found: {model_path}. Put a pretrained YOLO .pt file in {MODELS_DIR}/")
    try:
        from ultralytics.utils.downloads import attempt_download_asset

        log.info("Downloading pretrained model %s to %s ...", name, MODELS_DIR)
        result = attempt_download_asset(str(MODELS_DIR / name))
        path = Path(result)
        if not path.is_file():
            raise DetectorError(f"Download of {name} failed")
        return path
    except DetectorError:
        raise
    except Exception as exc:
        raise DetectorError(f"Cannot download model {name}: {exc}") from exc


def tracking_available() -> bool:
    try:
        import lap  # noqa: F401  (ultralytics ByteTrack dependency)

        return True
    except Exception:
        return False


class YoloDetector(BaseDetector):
    def __init__(self) -> None:
        self._model = None
        self._config = DetectorConfig()
        self._info = DetectorInfo()
        self._lock = threading.Lock()
        self._tracking = False

    # ------------------------------------------------------------------ lifecycle
    def load(self, config: DetectorConfig) -> DetectorInfo:
        with self._lock:
            self.unload()
            try:
                from ultralytics import YOLO
            except Exception as exc:
                raise DetectorError(f"ultralytics not installed: {exc}") from exc

            path = resolve_model_path(config.model_path, config.auto_download)
            device = resolve_device(config.device)
            try:
                model = YOLO(str(path))
                # Move once; predict() also receives device but this catches driver issues early.
                model.to(device)
            except Exception as exc:
                if device != "cpu":
                    log.warning("Loading on %s failed (%s), retrying on CPU", device, exc)
                    device = "cpu"
                    try:
                        model = YOLO(str(path))
                        model.to("cpu")
                    except Exception as exc2:
                        raise DetectorError(f"Cannot load model {path}: {exc2}") from exc2
                else:
                    raise DetectorError(f"Cannot load model {path}: {exc}") from exc

            self._tracking = bool(config.tracking_enabled)
            if self._tracking and not tracking_available():
                log.warning("Tracking requested but 'lap' is missing (pip install lap). Running detection only.")
                self._tracking = False

            self._model = model
            self._config = config
            names = getattr(model, "names", {}) or {}
            self._info = DetectorInfo(
                model_name=path.name,
                model_path=str(path),
                device=device,
                classes=list(config.classes),
                tracking=self._tracking,
                backend="ultralytics",
            )
            # Warm-up (allocates CUDA context / builds graph) so first real frame is fast.
            try:
                dummy = np.zeros((max(64, config.imgsz), max(64, config.imgsz), 3), dtype=np.uint8)
                self._run(dummy)
            except Exception as exc:
                self._model = None
                raise DetectorError(f"Model warm-up failed on {device}: {exc}") from exc
            log.info("YOLO loaded: %s (classes=%s -> %s)", self._info.summary(),
                     config.classes, [names.get(c, c) for c in config.classes])
            return self._info

    def unload(self) -> None:
        self._model = None
        self._info = DetectorInfo()

    def is_loaded(self) -> bool:
        return self._model is not None

    def info(self) -> DetectorInfo:
        return self._info

    def update_thresholds(self, confidence: float, iou: float) -> None:
        self._config.confidence = float(confidence)
        self._config.iou = float(iou)

    def reset_tracker(self) -> None:
        model = self._model
        if model is None:
            return
        try:
            predictor = getattr(model, "predictor", None)
            trackers = getattr(predictor, "trackers", None)
            if trackers:
                for t in trackers:
                    t.reset()
        except Exception as exc:
            log.debug("reset_tracker: %s", exc)

    # ------------------------------------------------------------------ inference
    def _run(self, image: np.ndarray):
        cfg = self._config
        kwargs = dict(
            source=image,
            conf=float(cfg.confidence),
            iou=float(cfg.iou),
            classes=list(cfg.classes) or None,
            imgsz=int(cfg.imgsz),
            max_det=int(cfg.max_det),
            device=self._info.device,
            verbose=False,
        )
        if cfg.half and self._info.device.startswith("cuda"):
            kwargs["half"] = True  # only pass when wanted (newer ultralytics warns on the kwarg)
        if self._tracking:
            return self._model.track(persist=True, tracker=cfg.tracker, **kwargs)
        return self._model.predict(**kwargs)

    def detect(self, image: np.ndarray) -> List[Detection]:
        model = self._model
        if model is None:
            raise DetectorError("Model not loaded")
        try:
            results = self._run(image)
        except Exception as exc:
            if self._tracking:
                log.error("Tracking failed (%s) - falling back to detection only", exc)
                self._tracking = False
                self._info.tracking = False
                return self.detect(image)
            raise DetectorError(f"Inference failed: {exc}") from exc
        if not results:
            return []
        r = results[0]
        boxes = getattr(r, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return []
        names = getattr(r, "names", {}) or {}
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        ids: Optional[np.ndarray] = None
        if getattr(boxes, "id", None) is not None:
            ids = boxes.id.cpu().numpy().astype(int)
        out: List[Detection] = []
        for i in range(len(xyxy)):
            x1, y1, x2, y2 = (float(v) for v in xyxy[i])
            cid = int(clss[i])
            out.append(
                Detection(
                    bbox=BBox(x1, y1, x2, y2),
                    confidence=float(confs[i]),
                    class_id=cid,
                    class_name=str(names.get(cid, cid)),
                    track_id=int(ids[i]) if ids is not None else None,
                )
            )
        return out
