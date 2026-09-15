"""Pure 2D geometry used by ROI logic and the ROI editor. Works for normalized or pixel coords."""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]
Polygon = Sequence[Point]


def point_in_polygon(pt: Point, poly: Polygon) -> bool:
    """Ray-casting point-in-polygon (even-odd). Works for concave polygons."""
    n = len(poly)
    if n < 3:
        return False
    x, y = pt
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            x_int = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < x_int:
                inside = not inside
        j = i
    return inside


def polygon_area(poly: Polygon) -> float:
    """Shoelace formula (absolute area)."""
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def polygon_centroid(poly: Polygon) -> Point:
    n = len(poly)
    if n == 0:
        return (0.0, 0.0)
    if n < 3:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    a = 0.0
    cx = cy = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = x1 * y2 - x2 * y1
        a += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a) < 1e-12:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    a *= 0.5
    return (cx / (6.0 * a), cy / (6.0 * a))


def bounding_rect(poly: Polygon) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def clip_polygon_to_rect(poly: Polygon, x1: float, y1: float, x2: float, y2: float) -> List[Point]:
    """Sutherland-Hodgman clipping of an arbitrary polygon against an axis-aligned rect."""
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1

    def clip(points: List[Point], inside, intersect) -> List[Point]:
        out: List[Point] = []
        n = len(points)
        if n == 0:
            return out
        prev = points[-1]
        prev_in = inside(prev)
        for cur in points:
            cur_in = inside(cur)
            if cur_in:
                if not prev_in:
                    out.append(intersect(prev, cur))
                out.append(cur)
            elif prev_in:
                out.append(intersect(prev, cur))
            prev, prev_in = cur, cur_in
        return out

    def inter_x(a: Point, b: Point, x: float) -> Point:
        if b[0] == a[0]:
            return (x, a[1])
        t = (x - a[0]) / (b[0] - a[0])
        return (x, a[1] + t * (b[1] - a[1]))

    def inter_y(a: Point, b: Point, y: float) -> Point:
        if b[1] == a[1]:
            return (a[0], y)
        t = (y - a[1]) / (b[1] - a[1])
        return (a[0] + t * (b[0] - a[0]), y)

    pts = list(poly)
    pts = clip(pts, lambda p: p[0] >= x1, lambda a, b: inter_x(a, b, x1))
    pts = clip(pts, lambda p: p[0] <= x2, lambda a, b: inter_x(a, b, x2))
    pts = clip(pts, lambda p: p[1] >= y1, lambda a, b: inter_y(a, b, y1))
    pts = clip(pts, lambda p: p[1] <= y2, lambda a, b: inter_y(a, b, y2))
    return pts


def bbox_polygon_intersection_ratio(bbox: Tuple[float, float, float, float], poly: Polygon) -> float:
    """(area of bbox ∩ polygon) / (area of bbox), in [0, 1]."""
    x1, y1, x2, y2 = bbox
    bw, bh = abs(x2 - x1), abs(y2 - y1)
    box_area = bw * bh
    if box_area <= 0 or len(poly) < 3:
        return 0.0
    # Fast reject using bounding rects.
    px1, py1, px2, py2 = bounding_rect(poly)
    if px2 < min(x1, x2) or px1 > max(x1, x2) or py2 < min(y1, y2) or py1 > max(y1, y2):
        return 0.0
    clipped = clip_polygon_to_rect(poly, x1, y1, x2, y2)
    inter = polygon_area(clipped)
    return max(0.0, min(1.0, inter / box_area))


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def distance_point_segment(p: Point, a: Point, b: Point) -> Tuple[float, float]:
    """Return (distance, t) where t in [0,1] is the projection parameter along a->b."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 <= 1e-12:
        return distance(p, a), 0.0
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / seg_len2
    t = max(0.0, min(1.0, t))
    proj = (ax + t * dx, ay + t * dy)
    return distance(p, proj), t


def nearest_vertex(poly: Polygon, p: Point, tolerance: float) -> Optional[int]:
    best_i, best_d = None, tolerance
    for i, v in enumerate(poly):
        d = distance(v, p)
        if d <= best_d:
            best_i, best_d = i, d
    return best_i


def nearest_edge(poly: Polygon, p: Point, tolerance: float) -> Optional[Tuple[int, Point]]:
    """Return (edge_index, projected_point) of the closest edge within tolerance.

    Edge i connects poly[i] -> poly[(i+1) % n]. Inserting at index i+1 splits it.
    """
    n = len(poly)
    best: Optional[Tuple[int, Point]] = None
    best_d = tolerance
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        d, t = distance_point_segment(p, a, b)
        if d <= best_d:
            best_d = d
            best = (i, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return best


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
