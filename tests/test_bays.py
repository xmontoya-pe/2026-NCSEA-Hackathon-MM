"""Tests for gravity.bays (spec 6, prompt 5): framing per bay, depth check, ranking."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from gravity import db_adapter
from gravity.bays import _choose, allowable_depth_in, evaluate, evaluate_all
from gravity.db_adapter import BayFraming
from gravity.grid import Grid, layout
from gravity.loads import resolve_zones
from gravity.models import GravityInput, ObjectiveWeights

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
HAS_DB = db_adapter.db_path().is_file()


def raw(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def grid_30(x_end: float, y_end: float) -> Grid:
    xs = tuple(float(v) for v in range(0, int(x_end) + 1, 30))
    ys = tuple(float(v) for v in range(0, int(y_end) + 1, 30))
    return Grid("test", xs, ys, "test")


def evaluate_grid(input_dict: dict, grid: Grid):
    inp = GravityInput.model_validate(input_dict)
    zm = resolve_zones(inp)
    return evaluate(inp, zm, layout(inp, zm, grid))


class TestAllowableDepth(unittest.TestCase):
    def test_uses_story_below(self):
        inp = GravityInput.model_validate(raw("simple_rect.json"))
        # L2 framing sits in the L1 story (15 ft): (15 - 9) * 12 - 18 = 54 in
        self.assertEqual(allowable_depth_in(inp, "L2"), 54)
        # L3 and ROOF framing sit in 13 ft stories: (13 - 9) * 12 - 18 = 30 in
        self.assertEqual(allowable_depth_in(inp, "L3"), 30)
        self.assertEqual(allowable_depth_in(inp, "ROOF"), 30)

    def test_override(self):
        d = raw("simple_rect.json")
        d["constraints"]["max_structure_depth_in"] = 24
        inp = GravityInput.model_validate(d)
        self.assertEqual(allowable_depth_in(inp, "L2"), 24)


def option(db_row, cost, depth, carbon):
    return BayFraming(
        db_row=db_row, db_system="STL_COMP", orientation="beams_span_x", beam_span_ft=30,
        girder_span_ft=30, spacing_ft=10, criterion="c", design_ll_psf=50, lookup_ll_psf=50,
        slab="s", beam="b", girder="g", depth_in=depth, steel_psf=1, slab_deck_psf=1,
        self_wt_psf=2, cost_per_sf=cost, carbon_kgco2e_per_sf=carbon,
    )


class TestChoose(unittest.TestCase):
    OPTIONS = [option(1, 10.0, 30.0, 9.0), option(2, 12.0, 20.0, 8.0), option(3, 11.0, 25.0, 7.0)]

    def test_weights_drive_the_choice(self):
        self.assertEqual(_choose(self.OPTIONS, 99, ObjectiveWeights(cost=1, depth=0, carbon=0)).db_row, 1)
        self.assertEqual(_choose(self.OPTIONS, 99, ObjectiveWeights(cost=0, depth=1, carbon=0)).db_row, 2)
        self.assertEqual(_choose(self.OPTIONS, 99, ObjectiveWeights(cost=0, depth=0, carbon=1)).db_row, 3)

    def test_options_within_depth_preferred(self):
        # Cheapest (row 1, 30 in) is over a 26 in limit; best within depth by cost is row 3.
        self.assertEqual(_choose(self.OPTIONS, 26, ObjectiveWeights(cost=1, depth=0, carbon=0)).db_row, 3)

    def test_none_within_depth_takes_shallowest(self):
        self.assertEqual(_choose(self.OPTIONS, 10, ObjectiveWeights(cost=1, depth=0, carbon=0)).db_row, 2)


@unittest.skipUnless(HAS_DB, "bay study DB not present")
class TestEvaluate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = evaluate_grid(raw("simple_rect.json"), grid_30(150, 90))
        cls.by_key = {(b.level, b.rect.bay_id): b for b in cls.result.bays}

    def test_every_bay_framed(self):
        self.assertEqual(len(self.result.bays), 45)  # 15 bays x (L2, L3, ROOF)
        self.assertEqual(self.result.n_infeasible, 0)

    def test_office_bay_uses_walking_envelope(self):
        b = self.by_key[("L2", "A1-B2")]
        self.assertEqual(b.loads.governing_occupancy, "office")
        self.assertEqual(b.loads.lookup_ll_psf, 65)
        self.assertEqual(b.loads.criterion, "Walking b=0.03")
        self.assertEqual(b.framing.db_system, "STL_COMP")
        self.assertTrue(any("Envelope" in w for w in b.warnings))

    def test_corridor_bay_rounds_up_to_ll_100(self):
        b = self.by_key[("L2", "E1-F2")]
        self.assertEqual(b.loads.governing_occupancy, "corridor_above_first")
        self.assertIsNone(b.loads.criterion)
        self.assertEqual(b.framing.design_ll_psf, 100)

    def test_roof_bays_use_roof_systems(self):
        for (level, _), b in self.by_key.items():
            if level == "ROOF":
                self.assertIn(b.framing.db_system, {"STL_ROOF_W", "STL_ROOF_J"})
                self.assertEqual(b.loads.lookup_ll_psf, 20)

    def test_depth_flags_match_allowable(self):
        for b in self.result.bays:
            self.assertEqual(b.depth_ok, b.framing.depth_in <= b.allowable_depth_in + 1e-6)

    def test_totals(self):
        r = self.result
        self.assertAlmostEqual(r.area_sf, 3 * 150 * 90)
        self.assertAlmostEqual(r.framing_cost_usd,
                               sum(b.framing.cost_per_sf * b.area_sf for b in r.bays))
        self.assertGreater(r.framing_cost_usd / r.area_sf, 0)


@unittest.skipUnless(HAS_DB, "bay study DB not present")
class TestInfeasibleAndDepth(unittest.TestCase):
    def test_merged_long_span_bays_are_infeasible_with_warning(self):
        r = evaluate_grid(raw("no_column_zone.json"), grid_30(120, 90))
        merged = [b for b in r.bays if b.rect.merged]
        self.assertEqual(len(merged), 9)  # 3 per framed level
        self.assertEqual(r.n_infeasible, 9)
        self.assertTrue(all(b.framing is None for b in merged))
        self.assertTrue(any("60 x 30 ft exceeds the steel bay study" in w
                            and "long-span or transfer" in w for w in r.warnings))

    def test_tight_depth_limit_flags_every_bay(self):
        d = raw("simple_rect.json")
        d["constraints"]["max_structure_depth_in"] = 10
        r = evaluate_grid(d, grid_30(150, 90))
        self.assertEqual(r.n_over_depth, 45)
        self.assertTrue(any("over the 10 in allowable" in w for w in r.warnings))


@unittest.skipUnless(HAS_DB, "bay study DB not present")
class TestEvaluateAll(unittest.TestCase):
    def test_ranked_best_first(self):
        results, _ = evaluate_all(GravityInput.model_validate(raw("simple_rect.json")))
        self.assertTrue(results)
        keys = [r.rank_key() for r in results]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(results[0].n_infeasible, 0)

    def test_summary_cli(self):
        proc = subprocess.run(
            [sys.executable, "-m", "gravity", "summary", str(FIXTURES / "simple_rect.json")],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("X:30x5 Y:30x3", proc.stdout)
        self.assertIn("Best:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
