"""numpy (BGR) <-> QImage conversion helpers (UI thread only)."""
from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage


def bgr_to_qimage(image: np.ndarray) -> QImage:
    """Convert an OpenCV BGR (or gray) ndarray into a QImage that owns its memory."""
    if image is None:
        return QImage()
    if image.ndim == 2:
        img = np.ascontiguousarray(image)
        h, w = img.shape
        qimg = QImage(img.data, w, h, img.strides[0], QImage.Format.Format_Grayscale8)
        return qimg.copy()
    if image.shape[2] == 4:
        img = np.ascontiguousarray(image)
        h, w, _ = img.shape
        return QImage(img.data, w, h, img.strides[0], QImage.Format.Format_ARGB32).copy()
    img = np.ascontiguousarray(image)
    h, w, _ = img.shape
    return QImage(img.data, w, h, img.strides[0], QImage.Format.Format_BGR888).copy()
