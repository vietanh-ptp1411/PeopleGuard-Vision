import math

from visionguard.roi.geometry import (bbox_polygon_intersection_ratio, clip_polygon_to_rect, nearest_edge,
                                                nearest_vertex, point_in_polygon, polygon_area, polygon_centroid)

SQUARE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
CONCAVE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 0.5), (0.0, 1.0)]  # notch at the bottom


def test_point_in_square():
    assert point_in_polygon((0.5, 0.5), SQUARE)
    assert not point_in_polygon((1.5, 0.5), SQUARE)
    assert not point_in_polygon((0.5, -0.1), SQUARE)


def test_point_in_concave():
    assert point_in_polygon((0.05, 0.9), CONCAVE)     # below the V edge -> inside
    assert point_in_polygon((0.5, 0.4), CONCAVE)      # under the notch tip -> inside
    assert not point_in_polygon((0.5, 0.9), CONCAVE)  # inside the notch -> outside


def test_area_and_centroid():
    assert math.isclose(polygon_area(SQUARE), 1.0)
    cx, cy = polygon_centroid(SQUARE)
    assert math.isclose(cx, 0.5) and math.isclose(cy, 0.5)
    assert polygon_area([(0, 0), (1, 1)]) == 0.0


def test_clip_and_intersection_ratio():
    clipped = clip_polygon_to_rect(SQUARE, 0.5, 0.5, 2.0, 2.0)
    assert math.isclose(polygon_area(clipped), 0.25)
    assert math.isclose(bbox_polygon_intersection_ratio((0.0, 0.0, 0.5, 0.5), SQUARE), 1.0)
    assert math.isclose(bbox_polygon_intersection_ratio((0.5, 0.5, 1.5, 1.5), SQUARE), 0.25)
    assert bbox_polygon_intersection_ratio((2.0, 2.0, 3.0, 3.0), SQUARE) == 0.0
    assert bbox_polygon_intersection_ratio((0.0, 0.0, 0.0, 0.0), SQUARE) == 0.0


def test_editor_helpers():
    assert nearest_vertex(SQUARE, (0.02, 0.01), 0.05) == 0
    assert nearest_vertex(SQUARE, (0.5, 0.5), 0.05) is None
    hit = nearest_edge(SQUARE, (0.5, 0.01), 0.05)
    assert hit is not None and hit[0] == 0
    assert math.isclose(hit[1][0], 0.5) and math.isclose(hit[1][1], 0.0)
