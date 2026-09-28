import unittest

from visionguard.config.schemas import ContainmentMode
from visionguard.roi.roi_model import Roi, RoiType
from visionguard.roi.roi_processor import RoiProcessor
from visionguard.vision.detection import BBox, Detection


class FootBoundaryTests(unittest.TestCase):
    def evaluate(self, point, points, mode=ContainmentMode.FOOT_POINT, extra=()):
        x, y = point
        detection = Detection(BBox(x * 1000 - 10, y * 1000 - 100,
                                   x * 1000 + 10, y * 1000), 0.9, 0, "person")
        roi = Roi("zone", "Zone", points=points)
        return RoiProcessor(mode).evaluate([detection], 1000, 1000, [roi, *extra])

    def test_all_edges_and_vertices_both_windings(self):
        square = [(0, 0), (1, 0), (1, 1), (0, 1)]
        for polygon in (square, square[::-1]):
            for point in square + [(0.5, 0), (1, 0.5), (0.5, 1), (0, 0.5)]:
                with self.subTest(point=point, polygon=polygon):
                    result = self.evaluate(point, polygon)
                    self.assertTrue(result.any_occupied)
                    self.assertEqual(result.roi_counts["zone"], 1)

    def test_sloping_concave_edge_and_repeated_vertex(self):
        polygon = [(0, 0), (1, 0), (1, 1), (0.5, 0.5), (0, 1), (0, 1)]
        self.assertEqual(self.evaluate((0.75, 0.75), polygon).in_roi, 1)
        self.assertEqual(self.evaluate((0.5, 0.8), polygon).outside, 1)

    def test_roundoff_only_not_a_visible_expansion(self):
        polygon = [(0, 0), (1, 0), (1, 1), (0, 1)]
        self.assertEqual(self.evaluate((0.5, 1 + 1e-10), polygon).in_roi, 1)
        self.assertEqual(self.evaluate((0.5, 1.001), polygon).outside, 1)
        self.assertEqual(self.evaluate((0.5, 0.999), polygon).in_roi, 1)

    def test_exclusions_keep_existing_boundary_behavior(self):
        polygon = [(0, 0), (1, 0), (1, 1), (0, 1)]
        exclusion = Roi("exclude", "Exclude", type=RoiType.EXCLUDE, points=polygon)
        self.assertEqual(self.evaluate((0.5, 1), polygon, extra=[exclusion]).in_roi, 1)
        self.assertEqual(self.evaluate((0.5, 0.5), polygon, extra=[exclusion]).ignored, 1)


if __name__ == "__main__":
    unittest.main()
