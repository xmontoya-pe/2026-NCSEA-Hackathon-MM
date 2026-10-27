"""Tests for gravity.columns (spec 6, prompt 6), including the interior-column hand check.

Hand check (spec 4.5): interior column B-2 of simple_rect.json on the 30 x 30 ft grid,
with round-number loads so every step can be followed on paper.

  Tributary area: half-bay method, 15 x 15 ft from each of four 30 x 30 bays = 900 sf/level.
  Assumed loads:  floors D = 105 psf (framing 75 + SDL 15 + partitions 15), L = 50 psf;
                  roof D = 25 psf, Lr0 = 20 psf, no snow.
    ROOF: D = 25 x 900 = 22.5 k;  Lr0 = 20 x 900 = 18.0 k
    L3:   D = 105 x 900 = 94.5 k;  L = 50 x 900 = 45.0 k   (L2 the same)
  Roof Lr (ASCE 7-22 4.8): At = 900 >= 600 -> R1 = 0.6 -> Lr = 12 psf -> 10.8 k
  LL reduction (4.7), interior KLL = 4:
    story L2 carries 1 floor:  KLL*AT = 3600 -> 0.25 + 15/60 = 0.500 -> L = 45 x 0.5 = 22.50 k
    story L1 carries 2 floors: KLL*AT = 7200 -> 0.25 + 15/84.85 = 0.4268 (> 0.4)
                               -> L = 90 x 0.4268 = 38.41 k
  Pass 1 (no column weight) sizes L1-L2 as W12X53 and L3 as W12X40; pass 2 adds their weight:
    L3 seg 40 plf x 13 ft = 0.520 k; L2 seg 53 x 13 = 0.689 k; L1 seg 53 x 15 = 0.795 k
  Story L3: Pd = 22.5 + 0.520 = 23.020; Plr = 10.8
            1.4D = 32.23; 1.2D+1.6L+0.5Lr = 33.02; 1.2D+1.6Lr+1.0L = 27.62 + 17.28 = 44.90 <- Pu
  Story L2: Pd = 117.0 + 1.209 = 118.209; Pl = 22.50; Plr = 10.8
            1.2D+1.6L+0.5Lr = 141.85 + 36.00 + 5.40 = 183.25 <- Pu; 1.2D+1.6Lr+1.0L = 181.63
  Story L1: Pd = 211.5 + 2.004 = 213.504; Pl = 38.41; Plr = 10.8
            1.2D+1.6L+0.5Lr = 256.20 + 61.46 + 5.40 = 323.06 <- Pu; 1.2D+1.6Lr+1.0L = 311.89
  AISC 360-22 E3 (Fy = 50, E = 29000, K = 1; 4.71 sqrt(E/Fy) = 113.4, all inelastic):
    W12X53 (A 15.6, ry 2.48), L1 15 ft: KL/r = 180/2.48 = 72.58; Fe = pi^2 E/72.58^2 = 54.33
            Fcr = 0.658^(50/54.33) x 50 = 34.02; phiPn = 0.9 x 34.02 x 15.6 = 477.6
            ratio = 323.06/477.6 = 0.676
    W12X53, L2 13 ft: KL/r = 62.90; Fe = 72.34; Fcr = 37.44; phiPn = 525.6; ratio = 0.349
    W12X40 (A 11.7, ry 1.94), L3 13 ft: KL/r = 80.41; Fe = 44.26; Fcr = 31.16;
            phiPn = 328.1; ratio = 0.137
  Why W12X53: tier L1-L2 needs phiPn >= 323.06/0.90 = 359.0 k at 15 ft. Lighter non-slender
  shapes fall short (W12X50 354.6, W14X48 331.4, W12X45 316.2, W12X40 280.6); every W12
  under 40 lb and W14 under 48 lb is slender (h/tw > 35.88). At 53 lb, W12X53 (ratio 0.676)
  beats W14X53 (0.875). Tier L3 needs only 49.9 k: W12X40 is the lightest non-slender shape.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from gravity import db_adapter
from gravity.bays import evaluate
from gravity.columns import (
    LevelLoad,
    column_config,
    compression_capacity,
    design_columns,
    design_stack,
    factored,
    is_slender,
    load_shapes,
    seismic_weights,
)
from gravity.grid import Grid, layout
from gravity.loads import resolve_zones
from gravity.models import GravityInput

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
CFG = column_config("steel")
SHAPES_CSV = REPO_ROOT / CFG["shapes_csv"]
HAS_SHAPES = SHAPES_CSV.is_file()
HAS_DB = db_adapter.db_path().is_file()


def shapes():
    return load_shapes(SHAPES_CSV, tuple(CFG["families"]))


def by_name(name):
    return next(s for s in shapes() if s.name == name)


class Within1Percent(unittest.TestCase):
    def near(self, actual, expected, label=""):
        self.assertAlmostEqual(actual, expected, delta=max(abs(expected) * 0.01, 1e-6),
                               msg=f"{label}: {actual} vs {expected}")


@unittest.skipUnless(HAS_SHAPES, "data/aisc_shapes.csv not present")
class TestShapes(Within1Percent):
    def test_us_columns_read_not_metric(self):
        s = by_name("W12X53")
        self.assertEqual((s.weight_plf, s.area_in2, s.ry_in, s.rx_in), (53.0, 15.6, 2.48, 5.23))

    def test_only_w12_and_w14(self):
        self.assertTrue(all(s.name.startswith(("W12X", "W14X")) for s in shapes()))

    def test_slenderness(self):
        fy, e = CFG["fy_ksi"], CFG["e_ksi"]
        self.assertTrue(is_slender(by_name("W12X35"), fy, e))   # h/tw 36.2 > 35.88
        self.assertFalse(is_slender(by_name("W12X40"), fy, e))  # h/tw 33.6
        self.assertTrue(is_slender(by_name("W14X43"), fy, e))   # h/tw 37.4

    def test_e3_capacity_w12x53_15ft(self):
        c = compression_capacity(by_name("W12X53"), 15, 50, 29000, 1.0)
        self.near(c.kl_r, 72.58, "KL/r")
        self.near(c.fe_ksi, 54.33, "Fe")
        self.near(c.fcr_ksi, 34.02, "Fcr")
        self.near(c.phi_pn_kips, 477.6, "phiPn")
        self.assertFalse(c.elastic)


class TestCombos(Within1Percent):
    def test_governing_combo(self):
        pu, combo, values = factored(100, 50, 10, 0)
        self.assertEqual(combo, "1.2D+1.6L+0.5Lr")
        self.near(pu, 120 + 80 + 5)
        self.near(values["1.4D"], 140)
        self.near(values["1.2D+1.6Lr+1.0L"], 120 + 16 + 50)

    def test_snow_combo(self):
        _, combo, _ = factored(20, 0, 0, 30)
        self.assertEqual(combo, "1.2D+1.6S+1.0L")


@unittest.skipUnless(HAS_SHAPES, "data/aisc_shapes.csv not present")
class TestHandCheckInteriorColumn(Within1Percent):
    """The hand check in this module's docstring, number by number (within 1%)."""

    @classmethod
    def setUpClass(cls):
        loads = {
            "ROOF": LevelLoad("ROOF", at_sf=900, d_kips=22.5, lr0_kips=18.0),
            "L3": LevelLoad("L3", at_sf=900, d_kips=94.5, l_red_kips=45.0),
            "L2": LevelLoad("L2", at_sf=900, d_kips=94.5, l_red_kips=45.0),
        }
        stories = [("L1", 15.0), ("L2", 13.0), ("L3", 13.0)]
        supports = {"L1": ["L2", "L3", "ROOF"], "L2": ["L3", "ROOF"], "L3": ["ROOF"]}
        designs, cls.warnings = design_stack(
            stories, supports, loads, 4, shapes(), 0.90, 2, 50, 29000, 1.0)
        cls.story = {d.story: d for d in designs}

    def check(self, story, shape, pd, pl, plr, pu, combo, klr, fe, fcr, phi_pn, ratio):
        d = self.story[story]
        self.assertEqual(d.shape.name, shape)
        self.assertEqual(d.governing_combo, combo)
        for label, actual, expected in (
            ("Pd", d.pd_kips, pd), ("Pl", d.pl_kips, pl), ("Plr", d.plr_kips, plr),
            ("Pu", d.pu_kips, pu), ("KL/r", d.capacity.kl_r, klr), ("Fe", d.capacity.fe_ksi, fe),
            ("Fcr", d.capacity.fcr_ksi, fcr), ("phiPn", d.capacity.phi_pn_kips, phi_pn),
            ("ratio", d.ratio, ratio),
        ):
            self.near(actual, expected, f"{story} {label}")

    def test_story_l3(self):
        self.check("L3", "W12X40", 23.020, 0.0, 10.8, 44.90, "1.2D+1.6Lr+1.0L",
                   80.41, 44.26, 31.16, 328.1, 0.137)

    def test_story_l2(self):
        self.check("L2", "W12X53", 118.209, 22.50, 10.8, 183.25, "1.2D+1.6L+0.5Lr",
                   62.90, 72.34, 37.44, 525.6, 0.349)

    def test_story_l1(self):
        self.check("L1", "W12X53", 213.504, 38.41, 10.8, 323.06, "1.2D+1.6L+0.5Lr",
                   72.58, 54.33, 34.02, 477.6, 0.676)

    def test_reduction_and_combo_details(self):
        self.near(self.story["L1"].pl_unreduced_kips, 90.0, "L1 unreduced L")
        self.near(self.story["L1"].combos["1.2D+1.6Lr+1.0L"], 311.89, "L1 combo 3")
        self.near(self.story["L2"].combos["1.2D+1.6Lr+1.0L"], 181.63, "L2 combo 3")
        self.near(self.story["L3"].combos["1.4D"], 32.23, "L3 1.4D")
        self.assertIn("7200", self.story["L1"].ll_note)
        self.assertEqual(self.warnings, [])


@unittest.skipUnless(HAS_DB and HAS_SHAPES, "bay study DB or shapes CSV not present")
class TestDesignColumns(Within1Percent):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((FIXTURES / "simple_rect.json").read_text(encoding="utf-8"))
        cls.inp = GravityInput.model_validate(raw)
        cls.zm = resolve_zones(cls.inp)
        grid = Grid("X:30x5 Y:30x3", (0.0, 30.0, 60.0, 90.0, 120.0, 150.0),
                    (0.0, 30.0, 60.0, 90.0), "A")
        cls.candidate = evaluate(cls.inp, cls.zm, layout(cls.inp, cls.zm, grid))
        cls.design = design_columns(cls.inp, cls.zm, cls.candidate)

    def test_classification_and_kll(self):
        b2, b1, a1 = (self.design.column(c) for c in ("B-2", "B-1", "A-1"))
        self.assertEqual((b2.kind, b2.kll), ("interior", 4))
        self.assertEqual((b1.kind, b1.kll), ("edge", 2))
        self.assertEqual((a1.kind, a1.kll), ("corner", 4))
        self.assertEqual(len(self.design.columns), 24)

    def test_b2_tributary_and_dead_load_from_bays(self):
        b2 = self.design.column("B-2")
        for level in ("L2", "L3", "ROOF"):
            self.near(b2.level_loads[level].at_sf, 900, f"{level} AT")
        # ROOF D = sum over the four bays around B-2 of 225 sf x (self wt + SDL + partitions)
        around = {"A1-B2", "B1-C2", "A2-B3", "B2-C3"}
        expected = sum(
            b.area_sf / 4 * (b.framing.self_wt_psf + b.loads.sdl_psf + b.loads.partitions_psf)
            for b in self.candidate.bays if b.level == "ROOF" and b.rect.bay_id in around
        ) / 1000
        self.near(b2.level_loads["ROOF"].d_kips, expected, "ROOF D")
        top = b2.stories[-1]
        self.assertEqual(top.story, "L3")
        self.near(top.pd_kips, expected + top.self_weight_kips, "L3 Pd")

    def test_every_column_within_target(self):
        self.assertLessEqual(self.design.max_ratio, CFG["target_ratio"] + 1e-9)
        for c in self.design.columns:
            self.assertEqual([s.story for s in c.stories], ["L1", "L2", "L3"])
            self.assertEqual(c.stories[0].shape, c.stories[1].shape)  # 2-story tier

    def test_tonnage_cost_carbon(self):
        d = self.design
        tons = sum(s.shape.weight_plf * s.height_ft for c in d.columns for s in c.stories) / 2000
        self.near(d.tons, tons, "tons")
        self.near(d.cost_usd, tons * 5500, "cost")
        self.near(d.carbon_kgco2e, tons * 0.90718474 * 1220, "carbon")

    def test_seismic_weight_roof(self):
        results = {s.level: s for s in seismic_weights(self.inp, self.zm, self.candidate,
                                                       self.design)}
        self.assertEqual(list(results), ["L2", "L3", "ROOF"])
        bays = sum((b.framing.self_wt_psf + b.loads.sdl_psf + b.loads.partitions_psf)
                   * b.area_sf for b in self.candidate.bays if b.level == "ROOF") / 1000
        half_columns = sum(s.shape.weight_plf * s.height_ft / 2000
                           for c in self.design.columns for s in c.stories if s.story == "L3")
        self.near(results["ROOF"].weight_kips, bays + half_columns, "ROOF W")
        x, y = results["L2"].center_of_mass_ft
        self.assertTrue(0 < x < 150 and 0 < y < 90)

    def test_column_cli(self):
        proc = subprocess.run(
            [sys.executable, "-m", "gravity", "column", str(FIXTURES / "simple_rect.json"),
             "--column", "B-2", "--grid", "X:30x5 Y:30x3"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for text in ("Column B-2", "interior column, KLL = 4", "KL/r", "phiPn", "Seismic weight"):
            self.assertIn(text, proc.stdout)


if __name__ == "__main__":
    unittest.main()
