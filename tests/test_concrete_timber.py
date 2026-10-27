"""Concrete and mass timber (spec 6, prompt 8): DB mapping, costs, column capacity.

Hand checks (rows from `python -m gravity dbdump`, rates from config/costs.json):

  PT flat plate, 30 x 30 office + stud rails, bay_id 7736: 8 in. slab, rebar 0.94 psf,
  PT 0.96 psf, concrete 0.024691 cy/sf, 24 in. DB column.
    self weight = 8/12 x 150 pcf = 100.0 psf;  steel (rebar + PT) = 1.90 psf
    cost/sf: concrete 0.024691 x 300 = 7.407; formwork 9.000; rebar 0.94/2000 x 2800 = 1.316;
             PT 0.96 x 4.00 = 3.840; stud rails 1.000 -> 22.563

  Mass timber floor, 30 x 30 @ 15 ft, Walking b=0.03 -> DB 'Walking β=0.03 (office)',
  bay_id 6535: glulam 2.649 bf/sf, 5-ply CLT 6.875 in., 3 in. topping.
    self weight = 2.649 x 35/12 = 7.726 glulam + 20.6 CLT + 37.5 topping = 65.826 psf
    cost/sf: glulam 2.649 x 7 = 18.543; CLT 6.875/12 x 45 = 25.781; topping 6.000 -> 50.324
    timber = (2.649 + 6.875)/12 = 0.79367 cf/sf

  Concrete column, 16 in. square, 1% steel: Ag = 256, Ast = 2.56 in2
    phiPn,max = 0.80 x 0.65 x [0.85 x 5 x 253.44 + 60 x 2.56] = 0.52 x 1230.72 = 639.97 k

  Glulam column, 12.25 in. square, 15 ft, lambda 0.8 (Fc 2300, Emin 850,000 psi, c 0.9):
    Fc* = 2300 x 2.40 x 0.90 x 0.8 = 3974.4 psi; Emin' = 850,000 x 1.76 x 0.85 = 1,271,600 psi
    le/d = 180/12.25 = 14.694; FcE = 0.822 x 1,271,600 / 14.694^2 = 4841.1 psi
    a = FcE/Fc* = 1.2181; Cp = (1+a)/1.8 - sqrt(((1+a)/1.8)^2 - a/0.9) = 0.8260
    P' = 3974.4 x 0.8260 x 150.06 = 492.6 k
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from gravity import db_adapter
from gravity.bays import evaluate
from gravity.columns import (
    Section,
    column_config,
    concrete_phi_pn,
    concrete_sections,
    design_columns,
    glulam_capacity,
    timber_sections,
)
from gravity.db_adapter import available_loads, framing_options
from gravity.grid import Grid, layout
from gravity.loads import resolve_zones
from gravity.models import GravityInput

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
HAS_DB = db_adapter.db_path().is_file()
WALKING = "Walking b=0.03"


def by_row(options, db_row):
    matches = [o for o in options if o.db_row == db_row]
    if not matches:
        raise AssertionError(f"bay_id {db_row} not among {[o.db_row for o in options]}")
    return matches[0]


class TestSystemsConfig(unittest.TestCase):
    def test_families(self):
        self.assertEqual(db_adapter.variants("concrete"),
                         ["concrete_rc_fp", "concrete_pt_fp", "concrete_ow_ptb", "concrete_pan"])
        self.assertEqual(db_adapter.variants("steel"), ["steel"])
        self.assertEqual(db_adapter.variants("mass_timber"), ["mass_timber"])
        self.assertEqual(db_adapter.family("concrete_pan"), "concrete")
        self.assertEqual(db_adapter.label("concrete_pt_fp"), "PT flat plate")


class TestColumnCapacity(unittest.TestCase):
    def test_concrete_16in(self):
        cfg = column_config("concrete_pt_fp")
        s16 = concrete_sections(cfg)[0]
        self.assertEqual(s16.name, "16x16 conc")
        self.assertAlmostEqual(concrete_phi_pn(s16, cfg), 639.97, delta=0.1)
        self.assertAlmostEqual(s16.weight_plf, 256 / 144 * 150)

    def test_glulam_12_25in_15ft(self):
        cfg = column_config("mass_timber")
        s = timber_sections(cfg)[-1]
        self.assertEqual(s.name, "12.25x12.25 GL")
        p, note = glulam_capacity(s, 15.0, 0.8, cfg)
        self.assertAlmostEqual(p, 492.6, delta=492.6 * 0.01)
        self.assertIn("Cp = 0.826", note)

    def test_glulam_slenderness_limit(self):
        cfg = column_config("mass_timber")
        p, note = glulam_capacity(Section("6.75x6.75 GL", 11, 6.75, "mass_timber"), 30.0, 0.8, cfg)
        self.assertEqual(p, 0.0)  # le/d = 53.3 > 50
        self.assertIn("> 50", note)


@unittest.skipUnless(HAS_DB, "bay study DB not present")
class TestConcreteRows(unittest.TestCase):
    def test_pt_flat_plate_row(self):
        o = by_row(framing_options("concrete_pt_fp", "floor", 30, 30, 65, 15), 7736)
        self.assertEqual((o.beam, o.girder), ('8" slab', '24" sq col'))
        self.assertAlmostEqual(o.slab_deck_psf, 100.0)
        self.assertAlmostEqual(o.self_wt_psf, 100.0)
        self.assertAlmostEqual(o.steel_psf, 1.90)
        b = o.cost_breakdown
        self.assertAlmostEqual(b["concrete"], 7.407, places=3)
        self.assertAlmostEqual(b["formwork"], 9.0)
        self.assertAlmostEqual(b["rebar"], 1.316, places=3)
        self.assertAlmostEqual(b["pt"], 3.84)
        self.assertAlmostEqual(b["stud_rails"], 1.0)
        self.assertAlmostEqual(o.cost_per_sf, 22.563, places=2)
        self.assertEqual(o.timber_cf_per_sf, 0.0)

    def test_floor_and_roof_rows_are_kept_apart(self):
        floor = framing_options("concrete_rc_fp", "floor", 30, 30, 30, 15)
        roof = framing_options("concrete_rc_fp", "roof", 30, 30, 20, 20)
        self.assertTrue(floor and roof)
        self.assertTrue(all(o.criterion.startswith("Residential 40 psf") for o in floor))
        self.assertTrue(all(o.criterion.startswith("Roof: 30 psf snow") for o in roof))

    def test_loads_by_use(self):
        self.assertEqual(available_loads("concrete_rc_fp", "floor"), ([40.0, 65.0, 100.0], [15.0]))
        self.assertEqual(available_loads("concrete_rc_fp", "roof"), ([30.0], [20.0]))

    def test_vibration_criterion_not_in_concrete_study(self):
        options = framing_options("concrete_pan", "floor", 28, 28, 65, 15, criterion=WALKING)
        by_row(options, 8461)
        self.assertTrue(all(any("floor vibration not checked" in w for w in o.warnings)
                            for o in options))


@unittest.skipUnless(HAS_DB, "bay study DB not present")
class TestMassTimberRows(unittest.TestCase):
    def test_walking_row(self):
        o = by_row(framing_options("mass_timber", "floor", 30, 30, 65, 10, criterion=WALKING), 6535)
        self.assertEqual(o.criterion, "Walking β=0.03 (office)")
        self.assertEqual(o.beam, "6-3/4 x 40.5 @ 15ft")
        # Flush framing (ASSUMPTIONS W-6): 46.5 in girder + 6.875 CLT + 3 topping.
        self.assertAlmostEqual(o.depth_in, 46.5 + 6.875 + 3.0)
        self.assertAlmostEqual(o.self_wt_psf, 65.826, places=3)
        self.assertAlmostEqual(o.cost_breakdown["glulam"], 18.543, places=3)
        self.assertAlmostEqual(o.cost_breakdown["clt"], 25.781, places=3)
        self.assertAlmostEqual(o.cost_breakdown["topping"], 6.0)
        self.assertAlmostEqual(o.cost_per_sf, 50.324, places=3)
        self.assertAlmostEqual(o.timber_cf_per_sf, 0.79367, places=4)
        self.assertEqual(o.steel_psf, 0.0)

    def test_roof_has_no_topping(self):
        options = framing_options("mass_timber", "roof", 30, 30, 20, 15)
        self.assertTrue(options)
        self.assertTrue(all("topping" not in o.cost_breakdown for o in options))
        self.assertTrue(all(o.slab_deck_psf == 20.6 for o in options))


@unittest.skipUnless(HAS_DB, "bay study DB not present")
class TestSquareColumns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((FIXTURES / "simple_rect.json").read_text(encoding="utf-8"))
        cls.inp = GravityInput.model_validate(raw)
        cls.zm = resolve_zones(cls.inp)
        cls.grid = Grid("X:30x5 Y:30x3", (0.0, 30.0, 60.0, 90.0, 120.0, 150.0),
                        (0.0, 30.0, 60.0, 90.0), "A")

    def design(self, system):
        candidate = evaluate(self.inp, self.zm, layout(self.inp, self.zm, self.grid), system)
        return design_columns(self.inp, self.zm, candidate, system)

    def test_pt_flat_plate_columns_at_least_db_size(self):
        d = self.design("concrete_pt_fp")
        self.assertEqual(d.material, "concrete")
        for c in d.columns:
            sizes = {s.shape.name for s in c.stories}
            self.assertEqual(len(sizes), 1, c.column_id)  # one size up the height
            self.assertGreaterEqual(c.stories[0].shape.width_in, 24, c.column_id)
            self.assertLessEqual(max(s.ratio for s in c.stories), 1.0 + 1e-9)
        self.assertGreater(d.concrete_cy, 0)
        self.assertGreater(d.cost_usd, 0)
        self.assertIn("Concrete columns: slenderness ignored (spec 4.5); verify", d.warnings)

    def test_glulam_columns(self):
        d = self.design("mass_timber")
        widths = set(column_config("mass_timber")["widths_in"])
        for c in d.columns:
            self.assertEqual(len({s.shape.name for s in c.stories}), 1, c.column_id)
            self.assertIn(c.stories[0].shape.width_in, widths)
        self.assertGreater(d.timber_cf, 0)
        self.assertEqual(d.tons, 0.0)


if __name__ == "__main__":
    unittest.main()
