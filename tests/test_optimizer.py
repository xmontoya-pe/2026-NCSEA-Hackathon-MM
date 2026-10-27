"""Tests for scoring.py, optimizer.py and the real (non-stub) ``gravity.run``.

Run from the repo root:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import gravity
from gravity.models import GravityInput, GravityOutput, ObjectiveWeights
from gravity.scoring import Metrics, normalize, weighted_scores

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestScoring(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize([1.0, 2.0, 3.0]), [0.0, 0.5, 1.0])
        self.assertEqual(normalize([5.0, 5.0]), [0.0, 0.0])
        self.assertEqual(normalize([]), [])

    def test_weighted_scores(self):
        metrics = [Metrics(100, 20, 10), Metrics(200, 10, 10), Metrics(150, 30, 10)]
        w = ObjectiveWeights(cost=0.5, depth=0.3, carbon=0.2)
        # cost -> 0, 1, 0.5; depth -> 0.5, 0, 1; carbon flat -> 0
        expected = [0.5 * 0 + 0.3 * 0.5, 0.5 * 1 + 0.3 * 0, 0.5 * 0.5 + 0.3 * 1]
        for got, want in zip(weighted_scores(metrics, w), expected):
            self.assertAlmostEqual(got, want)


class TestRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inp = load("simple_rect.json")
        cls.result = gravity.run(cls.inp)
        cls.scheme = cls.result["schemes"][0]

    def test_steel_only_with_warnings_for_unbuilt_systems(self):
        self.assertEqual([s["system"] for s in self.result["schemes"]], ["steel"])
        for system in ("concrete", "mass_timber"):
            self.assertTrue(any(f"'{system}'" in w and "not built" in w
                                for w in self.scheme["warnings"]), system)

    def test_output_matches_contract_and_is_deterministic(self):
        GravityOutput.model_validate(self.result)
        self.assertEqual(gravity.run(copy.deepcopy(self.inp)), self.result)

    def test_alternates(self):
        alternates = self.scheme["alternates"]
        self.assertLessEqual(len(alternates), 3)
        grids = [json.dumps(a["grid"]) for a in alternates] + [json.dumps(self.scheme["grid"])]
        self.assertEqual(len(set(grids)), len(grids), "alternates repeat a grid")
        for a in alternates:
            self.assertEqual(a["alternates"], [])

    def test_bays_and_levels_are_framed_levels(self):
        framed = {"L2", "L3", "ROOF"}
        self.assertTrue(self.scheme["bays"])
        self.assertTrue({b["level"] for b in self.scheme["bays"]} <= framed)
        self.assertEqual([lv["level"] for lv in self.scheme["levels"]], ["L2", "L3", "ROOF"])

    def test_clear_height(self):
        # ASSUMPTIONS OUT-1: story below minus (deepest framing + plenum) / 12
        story_below = {"L2": 15.0, "L3": 13.0, "ROOF": 13.0}
        plenum = self.inp["constraints"]["plenum_allowance_in"]
        for lv in self.scheme["levels"]:
            want = story_below[lv["level"]] - (lv["max_structure_depth_in"] + plenum) / 12
            self.assertAlmostEqual(lv["clear_height_ft"], want, places=2)

    def test_level_depth_is_deepest_bay(self):
        for lv in self.scheme["levels"]:
            depths = [b["framing"]["depth_in"] for b in self.scheme["bays"] if b["level"] == lv["level"]]
            self.assertAlmostEqual(lv["max_structure_depth_in"], max(depths, default=0.0), places=3)
        self.assertAlmostEqual(self.scheme["totals"]["max_depth_in"],
                               max(lv["max_structure_depth_in"] for lv in self.scheme["levels"]), places=3)

    def test_foundation_reactions_are_bottom_segments(self):
        by_id = {c["id"]: c for c in self.scheme["columns"]}
        self.assertTrue(self.scheme["foundation_reactions"])
        for r in self.scheme["foundation_reactions"]:
            bottom = by_id[r["column_id"]]["stack"][0]
            self.assertEqual((r["D_kips"], r["L_kips"], r["Lr_kips"], r["S_kips"]),
                             (bottom["Pd_kips"], bottom["Pl_kips"], bottom["Plr_kips"], bottom["Psnow_kips"]))

    def test_no_column_zone_columns_absent(self):
        # z3 (30..90 x 30..60, no_columns at L1): columns strictly inside it are removed
        for c in self.scheme["columns"]:
            inside = 30 < c["x_ft"] < 90 and 30 < c["y_ft"] < 60
            self.assertFalse(inside, c["id"])

    def test_totals_positive(self):
        t, q = self.scheme["totals"], self.scheme["quantities"]
        self.assertGreater(t["cost_usd"], 0)
        self.assertGreater(t["cost_per_sf"], 0)
        self.assertGreater(q["steel_tons"], 0)

    def test_only_unbuilt_systems_gives_no_schemes(self):
        inp = copy.deepcopy(self.inp)
        inp["systems"] = ["concrete"]
        self.assertEqual(gravity.run(inp), {"schemes": []})

    def test_input_still_validates(self):
        GravityInput.model_validate(self.inp)


if __name__ == "__main__":
    unittest.main()
