"""Tests for gravity.grid (spec 6, prompt 5): candidate grids and layout. No DB needed."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from gravity.grid import Grid, _compress, _divide, generate_grids, layout, x_label
from gravity.loads import resolve_zones
from gravity.models import GravityInput

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Every beam or girder span in the steel bay study within 20..45 ft.
SIZES = [20, 22, 24, 26, 27, 28, 30, 32, 34, 36, 38, 40, 42, 44, 45]


def load(name: str) -> GravityInput:
    return GravityInput.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def grid_30(x_end: float, y_end: float) -> Grid:
    xs = tuple(float(v) for v in range(0, int(x_end) + 1, 30))
    ys = tuple(float(v) for v in range(0, int(y_end) + 1, 30))
    return Grid("test", xs, ys, "test")


class TestCandidates(unittest.TestCase):
    def setUp(self):
        self.inp = load("simple_rect.json")
        self.grids, self.warnings = generate_grids(self.inp, SIZES)

    def test_dimension_line_grid_is_a_candidate(self):
        by_id = {g.grid_id: g for g in self.grids}
        g = by_id["X:30x5 Y:30x3"]
        self.assertEqual(g.x_ft, (0.0, 30.0, 60.0, 90.0, 120.0, 150.0))
        self.assertEqual(g.y_ft, (0.0, 30.0, 60.0, 90.0))
        self.assertIn("A", g.strategy)

    def test_every_bay_within_limits_and_grid_fills_bounding_box(self):
        for g in self.grids:
            with self.subTest(grid=g.grid_id):
                self.assertEqual((g.x_ft[0], g.x_ft[-1], g.y_ft[0], g.y_ft[-1]), (0, 150, 0, 90))
                for lines in (g.x_ft, g.y_ft):
                    for p, q in zip(lines[:-1], lines[1:]):
                        self.assertTrue(20 - 1e-6 <= q - p <= 45 + 1e-6, f"bay {q - p}")

    def test_candidate_count_is_dozens_and_ids_unique_and_sorted(self):
        ids = [g.grid_id for g in self.grids]
        self.assertTrue(10 <= len(ids) <= 100, len(ids))
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(self.warnings, [])

    def test_deterministic(self):
        again, _ = generate_grids(self.inp, SIZES)
        self.assertEqual([g.grid_id for g in again], [g.grid_id for g in self.grids])

    def test_odd_end_bay_strategy(self):
        # 26 ft bays: 5 x 26 = 130, odd end bay 20 ft.
        self.assertTrue(any(g.grid_id.startswith("X:26x5+20 ") for g in self.grids))

    def test_bays_round_up_to_half_foot(self):
        # 150 ft in 7 bays: 6 x 21.5 + 21 (ASSUMPTIONS G-6), not 7 x 21.43.
        ids = [g.grid_id for g in self.grids]
        self.assertTrue(any(i.startswith("X:21.5x6+21 ") for i in ids), ids)
        for g in self.grids:
            for lines in (g.x_ft, g.y_ft):
                for p, q in zip(lines[:-1], lines[1:]):
                    self.assertAlmostEqual((q - p) * 2, round((q - p) * 2), 9, g.grid_id)

    def test_l_shape_grids_include_reentrant_corner_lines(self):
        grids, _ = generate_grids(load("l_shape.json"), SIZES)
        self.assertTrue(grids)
        for g in grids:
            self.assertIn(60.0, g.x_ft, g.grid_id)
            self.assertIn(60.0, g.y_ft, g.grid_id)


class TestHelpers(unittest.TestCase):
    def test_compress(self):
        self.assertEqual(_compress((0, 30, 60, 90)), "30x3")
        self.assertEqual(_compress((0, 26, 52, 78, 104, 130, 150)), "26x5+20")

    def test_divide_rounds_up_and_last_bay_takes_remainder(self):
        self.assertEqual(_divide(0.0, 100.0, 3), [33.5, 67.0, 100.0])  # 33.5, 33.5, 33
        self.assertEqual(_divide(10.0, 100.0, 4), [32.5, 55.0, 77.5, 100.0])  # exact 22.5
        self.assertEqual(_divide(0.0, 30.0, 1), [30.0])
        self.assertIsNone(_divide(0.0, 10.1, 20))  # 0.505 rounds up to 1.0; 19 x 1.0 > 10.1

    def test_x_labels(self):
        self.assertEqual([x_label(i) for i in (0, 1, 25, 26, 27)], ["A", "B", "Z", "AA", "AB"])


class TestLayout(unittest.TestCase):
    def test_simple_rect_dimension_grid(self):
        inp = load("simple_rect.json")
        lay = layout(inp, resolve_zones(inp), grid_30(150, 90))
        # z3 (no_columns, L1) runs 30-90 x 30-60: every nearby column is on its boundary,
        # so none is strictly inside and none is removed.
        self.assertEqual(lay.column_count, 24)
        self.assertEqual(set(lay.bays_by_level), {"L2", "L3", "ROOF"})
        for level, bays in lay.bays_by_level.items():
            self.assertEqual(len(bays), 15, level)
            self.assertFalse(any(b.merged for b in bays))
        ids = [b.bay_id for b in lay.bays_by_level["L2"]]
        self.assertEqual(ids[0], "A1-B2")
        self.assertEqual(ids[-1], "E3-F4")
        self.assertFalse(any("removed" in w for w in lay.warnings))

    def test_columns_strictly_inside_no_column_zone_are_removed_and_bays_merge(self):
        inp = load("simple_rect.json")
        grid = Grid("test", (0.0, 25.0, 50.0, 75.0, 100.0, 125.0, 150.0), (0.0, 45.0, 90.0), "t")
        lay = layout(inp, resolve_zones(inp), grid)
        # (50, 45) = C-2 and (75, 45) = D-2 are strictly inside z3 -> removed from story L1 up.
        self.assertEqual(len(lay.columns_by_story["L1"]), 7 * 3 - 2)
        self.assertTrue(any("Column C-2 removed for no-column zone z3" in w for w in lay.warnings))
        self.assertTrue(any("Column D-2 removed" in w for w in lay.warnings))
        for level in ("L2", "L3", "ROOF"):
            bays = lay.bays_by_level[level]
            merged = [b for b in bays if b.merged]
            # Smallest long-span bays: 25-100 x 0-45 and 25-100 x 45-90 (75 x 45 ft),
            # not 25 x 90 strips.
            self.assertEqual([(b.bay_id, b.span_x_ft, b.span_y_ft) for b in merged],
                             [("B1-E2", 75.0, 45.0), ("B2-E3", 75.0, 45.0)])
            self.assertEqual(len(bays), 12 - 6 + 2)

    def test_no_column_zone_fixture(self):
        # z2 (no_columns, L1) is 40-80 x 20-70: columns C-2 (60,30) and C-3 (60,60) inside.
        inp = load("no_column_zone.json")
        lay = layout(inp, resolve_zones(inp), grid_30(120, 90))
        merged = [(b.bay_id, b.span_x_ft, b.span_y_ft) for b in lay.bays_by_level["L2"] if b.merged]
        self.assertEqual(merged, [("B1-D2", 60.0, 30.0), ("B2-D3", 60.0, 30.0),
                                  ("B3-D4", 60.0, 30.0)])
        self.assertEqual(len(lay.bays_by_level["L2"]), 12 - 6 + 3)

    def test_l_shape_cells_outside_outline_excluded(self):
        inp = load("l_shape.json")
        lay = layout(inp, resolve_zones(inp), grid_30(150, 120))
        # 6 x 5 = 30 intersections; 6 lie outside the notch (x 90-150, y 90-120).
        self.assertEqual(lay.column_count, 24)
        # 5 x 4 = 20 cells; 6 in the notch (x 60-150, y 60-120).
        self.assertEqual(len(lay.bays_by_level["L2"]), 14)

    def test_column_near_outline_edge_flagged(self):
        raw = json.loads((FIXTURES / "simple_rect.json").read_text(encoding="utf-8"))
        raw["outline"] = [[0, 0], [151.5, 0], [151.5, 90], [0, 90]]
        inp = GravityInput.model_validate(raw)
        lay = layout(inp, resolve_zones(inp), grid_30(150, 90))
        # F-2 at (150, 30) is 1.5 ft inside the right edge (F-1 sits on the bottom edge).
        self.assertTrue(any("Column F-2 is 1.50 ft from the outline edge" in w
                            for w in lay.warnings))


if __name__ == "__main__":
    unittest.main()
