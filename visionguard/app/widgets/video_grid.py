"""The live view: one VideoView per camera, laid out automatically.

One camera fills the panel. Two sit side by side. Three or four go into a 2x2. The grid
owns the routing: a frame carries its camera index, so it lands on the right tile without
anyone having to keep a mapping.

Drawing zones stays a one-camera-at-a-time job - the tile you last clicked is the active
one, and every editor command goes there. Its border is highlighted so it is never a
guess which camera a new zone will belong to.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from ...config.schemas import VisualizationConfig
from ..theme import COLOR_ACCENT, COLOR_BORDER, COLOR_TEXT_MUTED
from .video_view import VideoView


class _Tile(QWidget):
    """One camera: its picture plus a caption strip that names it."""

    clicked = Signal(int)

    def __init__(self, index: int, vis: VisualizationConfig, fps_limit: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.index = index
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.view = VideoView(vis, fps_limit)
        lay.addWidget(self.view, 1)
        self.caption = QLabel(f"Camera {index + 1}")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.caption)
        # In grid view the picture is a button; maximised, it belongs to the ROI editor.
        self.click_to_zoom = False
        self.view.installEventFilter(self)
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        colour = COLOR_ACCENT if active else COLOR_BORDER
        self.setStyleSheet(f"_Tile {{ background: transparent; }}")
        self.view.setStyleSheet(f"border: {2 if active else 1}px solid {colour};")
        self.caption.setStyleSheet(
            f"color: {COLOR_ACCENT if active else COLOR_TEXT_MUTED}; font-size: 8.5pt;"
            f" font-weight: {'700' if active else '600'}; padding: 3px 0; background: transparent;")

    def set_caption(self, text: str) -> None:
        self.caption.setText(text)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (obj is self.view and self.click_to_zoom
                and event.type() == QEvent.Type.MouseButtonPress):
            self.clicked.emit(self.index)
            return True        # swallowed: a grid click means 'show me this camera'
        return False

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit(self.index)
        super().mousePressEvent(event)


class VideoGrid(QWidget):
    """Same public surface as a single VideoView, fanned out over N cameras."""

    roi_drawn = Signal(str, list, int)         # RoiType value, points, camera index
    roi_points_changed = Signal(str, list)
    roi_selected = Signal(str)
    roi_delete_requested = Signal(str)
    mode_changed = Signal(str)
    status_message = Signal(str)
    active_changed = Signal(int)
    maximize_changed = Signal(int)          # camera index, or -1 back in the grid

    def __init__(self, vis: VisualizationConfig, fps_limit: int = 30,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vis = vis
        self._fps_limit = fps_limit
        self._tiles: List[_Tile] = []
        self._active = 0
        self._maximized = -1
        self._placeholder = ""
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(3)
        self.set_count(1)

    # ------------------------------------------------------------------ layout
    def set_count(self, count: int, labels: Optional[Sequence[str]] = None) -> None:
        count = max(1, int(count))
        while len(self._tiles) > count:
            tile = self._tiles.pop()
            self._grid.removeWidget(tile)
            tile.setParent(None)
            tile.deleteLater()
        while len(self._tiles) < count:
            tile = _Tile(len(self._tiles), self._vis, self._fps_limit)
            tile.clicked.connect(self.set_maximized)
            self._connect(tile)
            self._tiles.append(tile)

        if self._maximized >= count:
            self._maximized = -1
        if self._active >= count:
            self._active = 0
        self._relayout()
        self.set_labels(labels or [f"Camera {i + 1}" for i in range(count)])
        self._apply_placeholder()
        self._refresh_active()

    def _relayout(self) -> None:
        count = len(self._tiles)
        for tile in self._tiles:
            self._grid.removeWidget(tile)
        if self._maximized >= 0:
            for tile in self._tiles:
                tile.setVisible(tile.index == self._maximized)
            self._grid.addWidget(self._tiles[self._maximized], 0, 0)
            columns, rows = 1, 1
        else:
            columns = 1 if count == 1 else 2 if count <= 4 else 3
            for i, tile in enumerate(self._tiles):
                tile.setVisible(True)
                self._grid.addWidget(tile, i // columns, i % columns)
            rows = int(math.ceil(count / columns))
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 1 if c < columns else 0)
        for r in range(self._grid.rowCount()):
            self._grid.setRowStretch(r, 1 if r < rows else 0)
        single = count == 1
        for tile in self._tiles:
            tile.caption.setVisible(not single)
            # only a tile sitting in the grid doubles as a button
            tile.click_to_zoom = not single and self._maximized < 0

    def _apply_placeholder(self) -> None:
        """The step-by-step hint only fits on a single tile; repeated four times it is noise."""
        text = self._placeholder if len(self._tiles) == 1 else ""
        for tile in self._tiles:
            tile.view.set_placeholder(text)

    def set_labels(self, labels: Sequence[str]) -> None:
        for i, tile in enumerate(self._tiles):
            tile.set_caption(labels[i] if i < len(labels) else f"Camera {i + 1}")

    def _connect(self, tile: _Tile) -> None:
        view, index = tile.view, tile.index
        view.roi_drawn.connect(lambda t, pts, i=index: self.roi_drawn.emit(t, pts, i))
        view.roi_points_changed.connect(self.roi_points_changed)
        view.roi_selected.connect(self.roi_selected)
        view.roi_delete_requested.connect(self.roi_delete_requested)
        view.mode_changed.connect(self.mode_changed)
        view.status_message.connect(self.status_message)

    # ------------------------------------------------------------------ active tile
    @property
    def count(self) -> int:
        return len(self._tiles)

    @property
    def active(self) -> int:
        return self._active

    def set_active(self, index: int) -> None:
        index = max(0, min(int(index), len(self._tiles) - 1))
        if index == self._active:
            return
        self._active = index
        self._refresh_active()
        self.active_changed.emit(index)

    @property
    def maximized(self) -> int:
        return self._maximized

    def set_maximized(self, index: int) -> None:
        """index < 0 puts the grid back."""
        index = int(index)
        if index >= len(self._tiles):
            index = -1
        if index == self._maximized:
            return
        self._maximized = index
        if index >= 0:
            self._active = index        # the camera you zoomed into is the one you draw on
        self._relayout()
        self._refresh_active()
        self.maximize_changed.emit(self._maximized)
        if index >= 0:
            self.active_changed.emit(index)

    def _refresh_active(self) -> None:
        for tile in self._tiles:
            tile.set_active(tile.index == self._active and len(self._tiles) > 1)

    def view(self, index: int = -1) -> VideoView:
        if index < 0:
            index = self._active
        return self._tiles[max(0, min(index, len(self._tiles) - 1))].view

    def views(self) -> List[VideoView]:
        return [t.view for t in self._tiles]

    # ------------------------------------------------------------------ routing
    def set_frame(self, frame) -> None:
        index = int(getattr(frame, "camera", 0))
        if 0 <= index < len(self._tiles):
            self._tiles[index].view.set_frame(frame)

    def set_result(self, result) -> None:
        index = int(getattr(result, "camera", 0))
        if 0 <= index < len(self._tiles):
            self._tiles[index].view.set_result(result)

    def set_rois(self, rois) -> None:
        """Each tile only draws the zones that belong to its own camera."""
        for tile in self._tiles:
            tile.view.set_rois([r for r in rois if int(getattr(r, "camera", 0)) == tile.index])

    # ------------------------------------------------------------------ fan out
    def _all(self, name: str, *args) -> None:
        for tile in self._tiles:
            getattr(tile.view, name)(*args)

    def set_area_status(self, text: str, color: str) -> None:
        self._all("set_area_status", text, color)

    def set_overlay_info(self, text: str) -> None:
        self._all("set_overlay_info", text)

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self._apply_placeholder()

    def set_display_mode(self, mode: str) -> None:
        self._all("set_display_mode", mode)

    def clear_image(self) -> None:
        self._all("clear_image")

    def clear_result(self) -> None:
        self._all("clear_result")

    def set_visualization(self, vis: VisualizationConfig) -> None:
        self._vis = vis
        self._all("set_visualization", vis)

    # ------------------------------------------------------------------ editor (active tile only)
    def start_drawing(self, rtype) -> None:
        self.view().start_drawing(rtype)

    def finish_drawing(self) -> None:
        self.view().finish_drawing()

    def cancel_drawing(self) -> None:
        self.view().cancel_drawing()

    def set_edit_mode(self, enabled: bool) -> None:
        self._all("set_edit_mode", enabled)

    def select_roi(self, roi_id: str) -> None:
        self._all("select_roi", roi_id)

    @property
    def selected_roi_id(self) -> str:
        return self.view().selected_roi_id

    @property
    def mode(self):
        return self.view().mode

    @property
    def has_image(self) -> bool:
        return any(t.view.has_image for t in self._tiles)

    def undo_point(self) -> None:
        self.view().undo_point()

    @property
    def _display_mode(self) -> str:
        return self.view()._display_mode
