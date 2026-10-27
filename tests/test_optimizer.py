"""Tests for gravity.scoring and gravity.optimizer (spec 4.6, 4.7; spec 6, prompt 7)."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from gravity import db_adapter
from gravity.columns import column_config, load_shapes
from gravity.models import GravityInput, ObjectiveWeights
from gravity.optimizer import N_ALTERNATES, optimize
from gravity.scoring import penalty, weighted_scores
from test_contract import FIXTURE_NAMES, FIXTURES, all_schemes, load_json, run_fixture

REPO_ROOT = Path(__file__).resolve().parent.parent
CFG = column_config("steel")
HAS_DATA = db_adapter.db_path().is_file() and (REPO_ROOT / CFG["shapes_csv"]).is_file()
WEIGHTS = ObjectiveWeights(cost=0.5, depth=0.3, carbon=0.2)


class TestScoring(unittest.TestCase):
    def test_min_max_normalized_weighted_sum(self):
        rows = [
            {"cost": 100.0, "depth": 20.0, "carbon": 10.0},
            {"cost": 200.0, "depth": 30.0, "carbon": 10.0},
            {"cost": 150.0, "depth": 25.0, "carbon": 10.0},
        ]
        # Row 2: cost 1.0, depth 1.0, carbon 0 (all equal) -> (0.5 + 0.3) / 1.0 = 0.8
        # Row 3: cost 0.5, depth 0.5 -> (0.25 + 0.15) / 1.0 = 0.4
        for actual, expected in zip(weighted_scores(rows, WEIGHTS), [0.0, 0.8, 0.4]):
            self.assertAlmostEqual(actual, expected)

    def test_weights_are_normalized_by_their_sum(self):
        rows = [{"cost": 0.0, "depth": 0.0, "carbon": 0.0},
                {"cost": 1.0, "depth": 1.0, "carbon": 1.0}]
        heavy = ObjectiveWeights(cost=5, depth=3, carbon=2)
        self.assertAlmostEqual(weighted_scores(rows, heavy)[1], 1.0)

    def test_zero_weights_score_zero(self):
        rows = [{"cost": 1.0, "depth": 2.0, "carbon": 3.0}, {"cost": 4.0, "depth": 5.0, "carbon": 6.0}]
        self.assertEqual(weighted_scores(rows, ObjectiveWeights(cost=0, depth=0, carbon=0)),
                         [0.0, 0.0])

    def test_penalties_outweigh_any_score(self):
        self.assertGreaterEqual(penalty(0, 1), 1.0)  # one bay over depth > max score of 1
        self.assertGreater(penalty(1, 0), penalty(0, 99))
        self.assertEqual(penalty(0, 0), 0.0)


@unittest.skipUnless(HAS_DATA, "bay study DB or shapes CSV not present")
class TestOptimizeSimpleRect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inp = GravityInput.model_validate(load_json(FIXTURES / "simple_rect.json"))
        cls.result = optimize(cls.inp)

    def test_ranked_best_first(self):
        keys = [e.rank_key() for e in self.result.ranked]
        self.assertEqual(keys, sorted(keys))
        self.assertGreater(len(keys), N_ALTERNATES)

    def test_rank_score_is_score_plus_penalty(self):
        for e in self.result.ranked:
            c = e.candidate
            self.assertAlmostEqual(e.rank_score, e.score + penalty(c.n_infeasible, c.n_over_depth))
            self.assertTrue(0.0 <= e.score <= 1.0)

    def test_best_is_feasible_and_within_depth(self):
        best = self.result.ranked[0].candidate
        self.assertEqual((best.n_infeasible, best.n_over_depth), (0, 0))

    def test_totals_add_framing_and_columns(self):
        e = self.result.ranked[0]
        self.assertAlmostEqual(e.cost_usd, e.candidate.framing_cost_usd + e.design.cost_usd)
        self.assertAlmostEqual(e.carbon_kgco2e,
                               e.candidate.framing_carbon_kgco2e + e.design.carbon_kgco2e)
        framing = sum(b.framing.steel_psf * b.area_sf for b in e.candidate.bays if b.framing) / 2000
        self.assertAlmostEqual(e.framing_tons, framing)


@unittest.skipUnless(HAS_DATA, "bay study DB or shapes CSV not present")
class TestSchemeOutput(unittest.TestCase):
    """run() output for every fixture, checked against itself and the input."""

    def each_scheme(self):
        for name in FIXTURE_NAMES:
            inp = load_json(FIXTURES / name)
            for scheme in all_schemes(run_fixture(name)):
                yield name, inp, scheme

    def test_alternates_ranked_after_best(self):
        for name in FIXTURE_NAMES:
            for top in run_fixture(name)["schemes"]:
                scores = [top["rank_score"]] + [a["rank_score"] for a in top["alternates"]]
                with self.subTest(fixture=name, system=top["system"]):
                    self.assertLessEqual(len(top["alternates"]), N_ALTERNATES)
                    self.assertEqual(scores, sorted(scores))
                    self.assertTrue(all(a["alternates"] == [] for a in top["alternates"]))
                    self.assertTrue(all(a["system"] == top["system"] for a in top["alternates"]))

    def test_foundation_reaction_is_base_of_stack(self):
        for name, _, scheme in self.each_scheme():
            stacks = {c["id"]: c["stack"][0] for c in scheme["columns"]}
            for r in scheme["foundation_reactions"]:
                base = stacks[r["column_id"]]
                with self.subTest(fixture=name, column=r["column_id"]):
                    self.assertEqual(base["level"], "L1")
                    self.assertEqual((r["D_kips"], r["L_kips"], r["Lr_kips"], r["S_kips"]),
                                     (base["Pd_kips"], base["Pl_kips"], base["Plr_kips"],
                                      base["Psnow_kips"]))

    def test_levels_depth_and_clear_height(self):
        for name, inp, scheme in self.each_scheme():
            heights = {lvl["name"]: lvl["floor_to_floor_ft"] for lvl in inp["levels"]}
            below = {"L2": "L1", "L3": "L2", "ROOF": "L3"}
            self.assertEqual([lv["level"] for lv in scheme["levels"]], ["L2", "L3", "ROOF"])
            for lv in scheme["levels"]:
                depths = [b["framing"]["depth_in"] for b in scheme["bays"]
                          if b["level"] == lv["level"]] or [0.0]  # all infeasible: depth 0
                expected = heights[below[lv["level"]]] - (max(depths) + 18) / 12
                with self.subTest(fixture=name, level=lv["level"]):
                    self.assertEqual(lv["max_structure_depth_in"], max(depths))
                    self.assertAlmostEqual(lv["clear_height_ft"], expected, places=2)
                    self.assertGreater(lv["seismic_weight_kips"], 0)
            self.assertEqual(scheme["totals"]["max_depth_in"],
                             max(lv["max_structure_depth_in"] for lv in scheme["levels"]))

    def test_steel_tons_include_columns(self):
        shapes = {s.name: s for s in load_shapes(REPO_ROOT / CFG["shapes_csv"],
                                                 tuple(CFG["families"]))}
        for name, inp, scheme in self.each_scheme():
            with self.subTest(fixture=name, system=scheme["system"]):
                self.assertGreater(scheme["quantities"]["concrete_cy"], 0)  # slabs or topping
            if scheme["system"] != "steel":
                continue
            heights = {lvl["name"]: lvl["floor_to_floor_ft"] for lvl in inp["levels"]}
            column_tons = sum(shapes[seg["size"]].weight_plf * heights[seg["level"]]
                              for c in scheme["columns"] for seg in c["stack"]) / 2000
            with self.subTest(fixture=name):
                self.assertGreater(scheme["quantities"]["steel_tons"], column_tons)

    def test_columns_inside_outline_and_named_on_grid(self):
        for name, inp, scheme in self.each_scheme():
            xs, ys = scheme["grid"]["x_ft"], scheme["grid"]["y_ft"]
            for c in scheme["columns"]:
                with self.subTest(fixture=name, column=c["id"]):
                    self.assertIn(c["x_ft"], xs)
                    self.assertIn(c["y_ft"], ys)
            if name == "l_shape.json":  # nothing in the notch (x > 60, y > 60)
                self.assertFalse([c for c in scheme["columns"]
                                  if c["x_ft"] > 60 and c["y_ft"] > 60])

    def test_no_column_zone_reported(self):
        top = run_fixture("no_column_zone.json")["schemes"][0]
        zone = (40, 80, 20, 70)  # z2 on L1: x 40-80, y 20-70
        inside = [c["id"] for c in top["columns"]
                  if zone[0] < c["x_ft"] < zone[1] and zone[2] < c["y_ft"] < zone[3]]
        self.assertEqual(inside, [])


@unittest.skipUnless(HAS_DATA, "bay study DB or shapes CSV not present")
class TestReportCli(unittest.TestCase):
    def test_report_on_all_fixtures(self):
        proc = subprocess.run(
            [sys.executable, "-m", "gravity", "report", *(str(FIXTURES / n) for n in FIXTURE_NAMES)],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in FIXTURE_NAMES:
            self.assertIn(name, proc.stdout)
        for text in ("framing t", "column t", "best", "alt1", "Warnings (best scheme",
                     "STEEL |", "CONCRETE |", "MASS_TIMBER |", "SYSTEM COMPARISON"):
            self.assertIn(text, proc.stdout)


if __name__ == "__main__":
    unittest.main()
