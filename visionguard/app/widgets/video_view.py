"""VideoView: aspect-correct live image + overlays (ROI, detections, status) + polygon ROI editor.

Coordinates:
    image px  <-> normalized (0..1)  <-> widget px (letterboxed target rect)
ROI points are always handled normalized so that window resizing never moves them.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...camera.base_camera import Frame
from ...config.schemas import VisualizationConfig
from ...logic.occupancy_state_machine import OccupancyState
from ...logic.pipeline import PipelineResult
from ...roi.geometry import nearest_edge, nearest_vertex, point_in_polygon, polygon_centroid
from ...roi.roi_model import NormPoint, Roi, RoiType
from ...utils.qt_image import bgr_to_qimage
from ...vision.detection import DetectionZoneStatus, EvaluatedDetection
from ..theme import COLOR_BORDER, COLOR_TEXT_DIM, COLOR_TEXT_MUTED, COLOR_VIDEO_BG, contrast_text


class EditorMode(str, Enum):
    NONE = "none"
    DRAWING = "drawing"
    EDIT = "edit"


VERTEX_TOL_PX = 9.0
EDGE_TOL_PX = 7.0

PLACEHOLDER_YOLO = (
    "Step 1  -  open the 'Camera' tab, pick a source (USB / Video file / RTSP), then Connect + Start\n"
    "Step 2  -  'YOLO' tab: Load Model        Step 3  -  'ROI' tab: draw the monitored zone\n"
    "Step 4  -  'PLC' tab: Connect or keep Simulation        Step 5  -  press START SYSTEM (F5)")
PLACEHOLDER_AI_CAMERA = (
    "Step 1  -  'Camera' tab: IP, user and password of the AI camera, then Test Camera + Test Event\n"
    "Step 2  -  'AI Event' tab: map each camera region to a PLC device\n"
    "Step 3  -  'PLC' tab: Connect or keep Simulation        Step 4  -  press START SYSTEM (F5)")


class VideoView(QWidget):
    roi_drawn = Signal(str, list)              # RoiType value, [(nx, ny), ...]
    roi_points_changed = Signal(str, list)     # roi id, [(nx, ny), ...]
    roi_selected = Signal(str)                 # roi id ("" = none)
    roi_delete_requested = Signal(str)         # Delete key on a selected ROI
    mode_changed = Signal(str)
    status_message = Signal(str)

    def __init__(self, vis: VisualizationConfig, ui_fps_limit: int = 30, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vis = vis
        self._image: Optional[QImage] = None
        self._img_w = 0
        self._img_h = 0
        self._result: Optional[PipelineResult] = None
        self._display_mode = "raw"
        self._rois: Tuple[Roi, ...] = ()
        self._roi_states: Dict[str, OccupancyState] = {}
        self._area_text = ""
        self._area_color = COLOR_TEXT_DIM
        self._overlay_info = ""
        self._placeholder = PLACEHOLDER_YOLO
        # editor
        self._mode = EditorMode.NONE
        self._draw_type = RoiType.INCLUDE
        self._draft: List[NormPoint] = []
        self._cursor: Optional[QPointF] = None
        self._selected_id = ""
        self._edit_points: Optional[List[NormPoint]] = None
        self._drag_vertex: Optional[int] = None
        self._drag_all_start: Optional[Tuple[NormPoint, List[NormPoint]]] = None
        self._dragged = False
        # repaint throttle
        self._dirty = False
        self._timer = QTimer(self)
        self._timer.setInterval(max(10, int(1000 / max(1, ui_fps_limit))))
        self._timer.timeout.connect(self._flush)
        self._timer.start()

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    # ================================================================== data in
    def set_visualization(self, vis: VisualizationConfig) -> None:
        self.vis = vis
        self._mark()

    def set_display_mode(self, mode: str) -> None:
        """'raw' shows camera frames, 'result' shows the frames processed by the AI."""
        if mode != self._display_mode:
            self._display_mode = mode
            if mode == "raw":
                self._result = None
            self._mark()

    def set_frame(self, frame: Frame) -> None:
        if self._display_mode != "raw":
            return
        self._set_image(frame.image)

    def set_result(self, result: PipelineResult) -> None:
        if self._display_mode != "result":
            return
        self._result = result
        self._roi_states = dict(result.roi_states)
        self._set_image(result.frame.image)

    def clear_result(self) -> None:
        self._result = None
        self._roi_states = {}
        self._mark()

    def clear_image(self) -> None:
        self._image = None
        self._result = None
        self._mark()

    def set_rois(self, rois: Sequence[Roi]) -> None:
        self._rois = tuple(rois)
        if self._selected_id and not any(r.id == self._selected_id for r in rois):
            self._selected_id = ""
            self.roi_selected.emit("")
        self._mark()

    def set_area_status(self, text: str, color: str) -> None:
        self._area_text = text
        self._area_color = color
        self._mark()

    def set_overlay_info(self, text: str) -> None:
        self._overlay_info = text
        self._mark()

    def set_placeholder(self, text: str) -> None:
        """Guidance shown while there is no picture; it depends on the detection mode."""
        self._placeholder = text
        self._mark()

    def _set_image(self, image) -> None:
        self._image = bgr_to_qimage(image)
        self._img_h, self._img_w = image.shape[:2]
        self._mark()

    def _mark(self) -> None:
        self._dirty = True

    def _flush(self) -> None:
        if self._dirty:
            self._dirty = False
            self.update()

    @property
    def has_image(self) -> bool:
        return self._image is not None

    # ================================================================== editor API
    @property
    def mode(self) -> EditorMode:
        return self._mode

    @property
    def selected_roi_id(self) -> str:
        return self._selected_id

    def start_drawing(self, rtype: RoiType) -> None:
        self._draw_type = rtype
        self._draft = []
        self._set_mode(EditorMode.DRAWING)
        self.status_message.emit(
            f"Drawing {rtype.label} ROI: left-click to add points, double-click / Enter to finish, right-click to undo, Esc to cancel"
        )
        self.setFocus()

    def finish_drawing(self) -> None:
        if self._mode != EditorMode.DRAWING:
            return
        pts = self._dedupe(self._draft)
        if len(pts) < 3:
            self.status_message.emit("Need at least 3 points to close a polygon")
            return
        self._draft = []
        self._set_mode(EditorMode.NONE)
        self.roi_drawn.emit(self._draw_type.value, [tuple(p) for p in pts])

    def cancel_drawing(self) -> None:
        self._draft = []
        if self._mode == EditorMode.DRAWING:
            self._set_mode(EditorMode.NONE)
            self.status_message.emit("Drawing cancelled")

    def undo_point(self) -> None:
        if self._mode == EditorMode.DRAWING and self._draft:
            self._draft.pop()
            self._mark()

    def set_edit_mode(self, enabled: bool) -> None:
        if enabled:
            self._draft = []
            self._set_mode(EditorMode.EDIT)
            self.status_message.emit("Edit ROI: click a ROI to select, drag vertices, Shift+click an edge to add a vertex, right-click a vertex to remove, drag inside to move")
        elif self._mode == EditorMode.EDIT:
            self._set_mode(EditorMode.NONE)

    def select_roi(self, roi_id: str) -> None:
        if roi_id != self._selected_id:
            self._selected_id = roi_id
            self._edit_points = None
            self.roi_selected.emit(roi_id)
            self._mark()

    def _set_mode(self, mode: EditorMode) -> None:
        if mode != self._mode:
            self._mode = mode
            self._drag_vertex = None
            self._drag_all_start = None
            self._edit_points = None
            self.setCursor(Qt.CursorShape.CrossCursor if mode == EditorMode.DRAWING else Qt.CursorShape.ArrowCursor)
            self.mode_changed.emit(mode.value)
        self._mark()

    @staticmethod
    def _dedupe(points: List[NormPoint], eps: float = 1e-3) -> List[NormPoint]:
        out: List[NormPoint] = []
        for p in points:
            if not out or abs(out[-1][0] - p[0]) > eps or abs(out[-1][1] - p[1]) > eps:
                out.append(p)
        if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= eps and abs(out[0][1] - out[-1][1]) <= eps:
            out.pop()
        return out

    # ================================================================== coordinate mapping
    def target_rect(self) -> QRectF:
        W, H = float(self.width()), float(self.height())
        if self._img_w <= 0 or self._img_h <= 0:
            return QRectF(0, 0, W, H)
        scale = min(W / self._img_w, H / self._img_h)
        tw, th = self._img_w * scale, self._img_h * scale
        return QRectF((W - tw) / 2.0, (H - th) / 2.0, tw, th)

    def to_widget(self, nx: float, ny: float) -> QPointF:
        r = self.target_rect()
        return QPointF(r.x() + nx * r.width(), r.y() + ny * r.height())

    def to_norm(self, pos: QPointF) -> NormPoint:
        r = self.target_rect()
        if r.width() <= 0 or r.height() <= 0:
            return (0.0, 0.0)
        nx = (pos.x() - r.x()) / r.width()
        ny = (pos.y() - r.y()) / r.height()
        return (min(1.0, max(0.0, nx)), min(1.0, max(0.0, ny)))

    def px_to_widget(self, x: float, y: float) -> QPointF:
        r = self.target_rect()
        if self._img_w <= 0 or self._img_h <= 0:
            return QPointF(x, y)
        return QPointF(r.x() + x / self._img_w * r.width(), r.y() + y / self._img_h * r.height())

    def _widget_points(self, pts: Sequence[NormPoint]) -> List[QPointF]:
        return [self.to_widget(x, y) for x, y in pts]

    def _norm_tolerance(self, px: float) -> Tuple[float, float]:
        r = self.target_rect()
        return (px / max(1.0, r.width()), px / max(1.0, r.height()))

    # ================================================================== mouse / keys
    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        pos = ev.position()
        if self._mode == EditorMode.DRAWING:
            if ev.button() == Qt.MouseButton.LeftButton:
                self._draft.append(self.to_norm(pos))
                self._mark()
            elif ev.button() == Qt.MouseButton.RightButton:
                self.undo_point()
            return
        if self._mode == EditorMode.EDIT:
            self._edit_press(ev, pos)
            return
        if ev.button() == Qt.MouseButton.LeftButton:  # plain click selects (for the ROI table)
            hit = self._hit_roi(self.to_norm(pos))
            self.select_roi(hit.id if hit else "")

    def _edit_press(self, ev: QMouseEvent, pos: QPointF) -> None:
        npt = self.to_norm(pos)
        sel = self._selected_roi()
        self._dragged = False
        if sel is not None:
            pts_widget = [(p.x(), p.y()) for p in self._widget_points(sel.points)]
            vi = nearest_vertex(pts_widget, (pos.x(), pos.y()), VERTEX_TOL_PX)
            if ev.button() == Qt.MouseButton.RightButton:
                if vi is not None and len(sel.points) > 3:
                    pts = list(sel.points)
                    pts.pop(vi)
                    self.roi_points_changed.emit(sel.id, pts)
                return
            if ev.button() != Qt.MouseButton.LeftButton:
                return
            if vi is not None:
                self._edit_points = list(sel.points)
                self._drag_vertex = vi
                return
            if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                hit = nearest_edge(pts_widget, (pos.x(), pos.y()), EDGE_TOL_PX)
                if hit is not None:
                    idx, _proj = hit
                    pts = list(sel.points)
                    pts.insert(idx + 1, npt)
                    self._edit_points = pts
                    self._drag_vertex = idx + 1
                    return
            if point_in_polygon(npt, sel.points):
                self._edit_points = list(sel.points)
                self._drag_all_start = (npt, list(sel.points))
                return
        if ev.button() == Qt.MouseButton.LeftButton:
            hit = self._hit_roi(npt)
            self.select_roi(hit.id if hit else "")

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        pos = ev.position()
        if self._mode == EditorMode.DRAWING:
            self._cursor = pos
            self._mark()
            return
        if self._mode == EditorMode.EDIT and self._edit_points is not None:
            npt = self.to_norm(pos)
            if self._drag_vertex is not None:
                self._edit_points[self._drag_vertex] = npt
                self._dragged = True
                self._mark()
            elif self._drag_all_start is not None:
                start, orig = self._drag_all_start
                dx, dy = npt[0] - start[0], npt[1] - start[1]
                # keep polygon inside the image
                min_x = min(p[0] for p in orig)
                max_x = max(p[0] for p in orig)
                min_y = min(p[1] for p in orig)
                max_y = max(p[1] for p in orig)
                dx = max(-min_x, min(1.0 - max_x, dx))
                dy = max(-min_y, min(1.0 - max_y, dy))
                self._edit_points = [(x + dx, y + dy) for x, y in orig]
                self._dragged = True
                self._mark()
            return
        if self._mode == EditorMode.EDIT:
            sel = self._selected_roi()
            if sel is not None:
                pts_widget = [(p.x(), p.y()) for p in self._widget_points(sel.points)]
                on_vertex = nearest_vertex(pts_widget, (pos.x(), pos.y()), VERTEX_TOL_PX) is not None
                self.setCursor(Qt.CursorShape.SizeAllCursor if on_vertex else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if self._mode == EditorMode.EDIT and self._edit_points is not None and ev.button() == Qt.MouseButton.LeftButton:
            pts = self._edit_points
            rid = self._selected_id
            inserted = self._drag_vertex is not None and self._drag_all_start is None and self._selected_roi() is not None \
                and len(pts) != len(self._selected_roi().points)  # type: ignore[union-attr]
            self._edit_points = None
            self._drag_vertex = None
            self._drag_all_start = None
            if rid and (self._dragged or inserted):
                self.roi_points_changed.emit(rid, [tuple(p) for p in pts])
            self._mark()

    def mouseDoubleClickEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if self._mode == EditorMode.DRAWING and ev.button() == Qt.MouseButton.LeftButton:
            # the first click of the double-click already appended a point; drop that duplicate
            if len(self._draft) >= 2:
                self._draft.pop()
            self.finish_drawing()

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        key = ev.key()
        if self._mode == EditorMode.DRAWING:
            if key == Qt.Key.Key_Escape:
                self.cancel_drawing()
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.finish_drawing()
            elif key in (Qt.Key.Key_Backspace, Qt.Key.Key_Z):
                self.undo_point()
            return
        if key == Qt.Key.Key_Delete and self._selected_id:
            self.roi_delete_requested.emit(self._selected_id)
            return
        if key == Qt.Key.Key_Escape and self._mode == EditorMode.EDIT:
            self.select_roi("")
            return
        super().keyPressEvent(ev)

    # ================================================================== helpers
    def _selected_roi(self) -> Optional[Roi]:
        for r in self._rois:
            if r.id == self._selected_id:
                return r
        return None

    def _hit_roi(self, npt: NormPoint) -> Optional[Roi]:
        # topmost (last drawn) first; prefer include ROIs when overlapping with exclusions of same size
        for r in reversed(self._rois):
            if r.is_valid() and point_in_polygon(npt, r.points):
                return r
        return None

    def _roi_color(self, roi: Roi) -> QColor:
        if roi.color:
            return QColor(roi.color)
        if roi.is_exclude:
            return QColor(self.vis.color_roi_exclude)
        st = self._roi_states.get(roi.id)
        if st is not None and st.occupied:
            return QColor(self.vis.color_roi_include_occupied)
        return QColor(self.vis.color_roi_include)

    # ================================================================== painting
    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(COLOR_VIDEO_BG))
        if self._image is None:
            self._paint_placeholder(p)
        else:
            rect = self.target_rect()
            p.drawImage(rect, self._image)
            p.setPen(QPen(QColor(COLOR_BORDER), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5))
        if self._img_w > 0:
            self._paint_rois(p)
            if self._result is not None:
                self._paint_detections(p, self._result.evaluation.detections)
            self._paint_draft(p)
        self._paint_banner(p)
        p.end()

    def _paint_placeholder(self, p: QPainter) -> None:
        r = self.rect()
        f = QFont()
        f.setPointSize(17)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(COLOR_TEXT_DIM))
        p.drawText(QRectF(r.x(), r.y(), r.width(), r.height() * 0.46),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, "NO VIDEO")
        f2 = QFont()
        f2.setPointSize(10)
        p.setFont(f2)
        p.setPen(QColor(COLOR_TEXT_MUTED))
        p.drawText(QRectF(r.x(), r.y() + r.height() * 0.5, r.width(), r.height() * 0.5),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self._placeholder)

    def _paint_rois(self, p: QPainter) -> None:
        lw = max(1, int(self.vis.line_width))
        # exclusions first so include outlines stay visible on top
        ordered = [r for r in self._rois if r.is_exclude] + [r for r in self._rois if r.is_include]
        for roi in ordered:
            if len(roi.points) < 2:
                continue
            pts = roi.points
            if roi.id == self._selected_id and self._edit_points is not None:
                pts = self._edit_points
            color = self._roi_color(roi)
            if not roi.enabled:
                color.setAlpha(110)
            poly = QPolygonF(self._widget_points(pts))
            selected = roi.id == self._selected_id
            pen = QPen(color, lw + (1 if selected else 0))
            if not roi.enabled:
                pen.setStyle(Qt.PenStyle.DotLine)
            elif roi.is_exclude:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            fill = QColor(color)
            if roi.is_exclude:
                fill.setAlpha(90)
                p.setBrush(QBrush(fill, Qt.BrushStyle.BDiagPattern))
            else:
                fill.setAlpha(45 if self._roi_states.get(roi.id, OccupancyState.CLEAR).occupied else 22)
                p.setBrush(QBrush(fill))
            if len(pts) >= 3:
                p.drawPolygon(poly)
            else:
                p.drawPolyline(poly)
            # label
            cx, cy = polygon_centroid(pts)
            lab = self.to_widget(cx, cy)
            st = self._roi_states.get(roi.id)
            tag = roi.type.label.upper() if roi.is_exclude else (st.value if st else "")
            lines = [f"{roi.id}  {roi.name}" + (f"  [{roi.plc_device}]" if roi.plc_device else "")]
            if tag:
                lines.append(tag)
            if not roi.enabled:
                lines.append("DISABLED")
            self._draw_label(p, lab, lines, color, centered=True, small=True)
            # vertices
            if selected or self._mode == EditorMode.EDIT:
                vr = 5 if selected else 3
                p.setPen(QPen(color.darker(150), 1))
                p.setBrush(QBrush(QColor(self.vis.color_point) if selected else color))
                for q in poly:
                    p.drawEllipse(q, vr, vr)

    def _paint_draft(self, p: QPainter) -> None:
        if self._mode != EditorMode.DRAWING:
            return
        color = QColor(self.vis.color_roi_editing)
        pts = self._widget_points(self._draft)
        p.setPen(QPen(color, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if len(pts) >= 2:
            p.drawPolyline(QPolygonF(pts))
        if pts and self._cursor is not None:
            dash = QPen(color, 1, Qt.PenStyle.DashLine)
            p.setPen(dash)
            p.drawLine(pts[-1], self._cursor)
            if len(pts) >= 2:
                p.drawLine(self._cursor, pts[0])
        p.setPen(QPen(color.darker(150), 1))
        p.setBrush(QBrush(QColor(self.vis.color_point)))
        for i, q in enumerate(pts):
            p.drawEllipse(q, 5, 5)
            self._draw_label(p, QPointF(q.x() + 8, q.y() - 8), [f"P{i + 1}"], color, small=True)
        hint = f"{self._draw_type.label} ROI - {len(pts)} point(s) - double-click/Enter to finish, Esc to cancel"
        self._draw_label(p, QPointF(12, self.height() - 30), [hint], color, small=True)

    def _paint_detections(self, p: QPainter, dets: Sequence[EvaluatedDetection]) -> None:
        lw = max(1, int(self.vis.line_width))
        for i, ev in enumerate(dets):
            det = ev.detection
            if ev.status == DetectionZoneStatus.IN_ROI:
                color = QColor(self.vis.color_person_in_roi)
            elif ev.status == DetectionZoneStatus.IGNORED_EXCLUSION:
                color = QColor(self.vis.color_person_ignored)
            else:
                color = QColor(self.vis.color_person)
            tl = self.px_to_widget(det.bbox.x1, det.bbox.y1)
            br = self.px_to_widget(det.bbox.x2, det.bbox.y2)
            rect = QRectF(tl, br)
            p.setPen(QPen(color, lw + (1 if ev.in_roi else 0)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect)
            if self.vis.show_points and ev.test_point is not None:
                q = self.px_to_widget(*ev.test_point)
                p.setBrush(QBrush(color))
                p.setPen(QPen(QColor(self.vis.color_point), 1))
                p.drawEllipse(q, 5, 5)
            if not self.vis.show_labels:
                continue
            ident = f"#{det.track_id}" if (self.vis.show_ids and det.track_id is not None) else f"{i + 1}"
            conf = f"  {det.confidence:.2f}" if self.vis.show_confidence else ""
            if ev.status == DetectionZoneStatus.IN_ROI:
                lines = ["PERSON IN AREA", ", ".join(ev.roi_ids) + conf]
            elif ev.status == DetectionZoneStatus.IGNORED_EXCLUSION:
                lines = ["IGNORED - EXCLUSION ZONE", f"Person {ident}{conf}"]
            else:
                lines = [f"Person {ident}{conf}"]
            self._draw_label(p, QPointF(rect.left(), rect.top() - 4), lines, color, above=True)

    def _paint_banner(self, p: QPainter) -> None:
        if not self._area_text:
            return
        f = QFont()
        f.setPointSize(max(11, min(20, self.width() // 55)))
        f.setBold(True)
        p.setFont(f)
        metrics = p.fontMetrics()
        text = self._area_text
        w = metrics.horizontalAdvance(text) + 28
        h = metrics.height() + 12
        rect = QRectF(12, 12, w, h)
        bg = QColor(self._area_color)
        bg.setAlpha(225)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(rect, 6, 6)
        p.setPen(QColor(contrast_text(self._area_color)))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        if self._overlay_info:
            sf = QFont()
            sf.setPointSize(9)
            p.setFont(sf)
            self._draw_label(p, QPointF(12, rect.bottom() + 6), self._overlay_info.split("\n"), QColor(COLOR_TEXT_DIM), small=True)

    def _draw_label(self, p: QPainter, pos: QPointF, lines: List[str], color: QColor, *, above: bool = False,
                    centered: bool = False, small: bool = False) -> None:
        f = QFont()
        f.setPointSize(8 if small else 9)
        f.setBold(True)
        p.setFont(f)
        metrics = p.fontMetrics()
        w = max(metrics.horizontalAdvance(t) for t in lines) + 10
        lh = metrics.height()
        h = lh * len(lines) + 6
        x, y = pos.x(), pos.y()
        if above:
            y -= h
        if centered:
            x -= w / 2
            y -= h / 2
        x = max(0.0, min(self.width() - w, x))
        y = max(0.0, min(self.height() - h, y))
        rect = QRectF(x, y, w, h)
        bg = QColor(color)
        bg.setAlpha(200)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(rect, 3, 3)
        p.setPen(QColor(contrast_text(color.name())))
        for i, t in enumerate(lines):
            p.drawText(QRectF(x + 5, y + 3 + i * lh, w - 10, lh), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, t)
