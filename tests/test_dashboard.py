"""Step 5 client dashboard (bones_service/dashboard.py): scoring, schedule, gravity + lateral.

Hand checks:

  Fit score, the design's sample (weights cost 4, schedule 3, carbon 3, seismic 2; sum 12):
                 cost  weeks  carbon  seismic
    concrete       57     44    1450     1.00
    steel          53     36    1210     0.64
    mass timber    63     31     690     0.52
  normalized (1 = lowest):
    concrete  cost (63-57)/10 = 0.6, schedule 0, carbon 0, seismic 0
              -> 4 x 0.6 / 12 = 0.200 -> 20
    steel     cost 1.0, schedule (44-36)/13 = 0.615, carbon (1450-1210)/760 = 0.316,
              seismic (1-0.64)/0.48 = 0.75
              -> (4 + 1.846 + 0.947 + 1.5) / 12 = 0.691 -> 69
    timber    cost 0, schedule 1, carbon 1, seismic 1 -> (3 + 3 + 2) / 12 = 0.667 -> 67

  Schedule, steel, 85 psf seismic weight, 8 floors (config/schedule.json):
    20,000 sf plate: foundations 4.5 + 3.0 x 0.85 = 7.05 wk; wait 14 - 7.05 = 6.95 wk;
                     superstructure 8 x 1.0 = 8.0 wk -> 22.0 wk
    80,000 sf plate: scale 4; foundations 7.05 x 2 = 14.1 wk; no wait;
                     cycle 1.0 x 4^0.6 = 2.2974 wk; 8 x 2.2974 = 18.379 -> 32.479 wk
"""

from __future__ import annotations

import copy
import unittest

from gravity import db_adapter

from bones_service import dashboard

HAS_DB = db_adapter.db_path().is_file()
WEIGHTS = {"cost": 4, "schedule": 3, "carbon": 3, "seismic": 2}


def metrics(cost, weeks, carbon, shear):
    return {"cost_usd": cost, "weeks": weeks, "carbon_t": carbon, "base_shear_kips": shear}


DESIGN_SAMPLE = [metrics(57, 44, 1450, 1.00), metrics(53, 36, 1210, 0.64), metrics(63, 31, 690, 0.52)]

# A 120 x 90 ft, 3-story pad state as BonesPad.state() would send it: one office load area
# over each floor, no frames drawn, and the site hazard step done.
WHOLE_FLOOR = {"la": [{"id": "1", "x1": 0, "y1": 0, "x2": 120, "y2": 90}], "laIds": [{"id": "1", "psf": 100}]}
STATE = {
    "project": "Dashboard test",
    "levels": [{"name": "", "h": 15.0, "psf": 100, "la": True},
               {"name": "", "h": 28.0, "psf": 100, "la": True},
               {"name": "", "h": 41.0, "psf": 30, "la": False}],
    "plan": {"verts": [[0, 0], [120, 0], [120, 90], [0, 90]], "closed": True, "lfrs": [],
             "lv": [WHOLE_FLOOR, WHOLE_FLOOR, {"la": [], "laIds": []}]},
    "sds": 1.0, "sd1": 0.6, "ie": 1.0,
    "bones": {"hazard": {"query": {"risk_category": "II"}},
              "loads": {"1": {"1": {"occupancy": "office"}}, "2": {"1": {"occupancy": "office"}}}},
}


class TestScoring(unittest.TestCase):
    def test_design_sample(self):
        self.assertEqual(dashboard.fit_scores(DESIGN_SAMPLE, WEIGHTS), [20, 69, 67])

    def test_all_weights_zero(self):
        zero = {k: 0 for k in WEIGHTS}
        self.assertEqual(dashboard.fit_scores(DESIGN_SAMPLE, zero), [0, 0, 0])

    def test_one_factor_only(self):
        # carbon alone: lowest carbon scores 100, highest 0
        only = {"cost": 0, "schedule": 0, "carbon": 5, "seismic": 0}
        self.assertEqual(dashboard.fit_scores(DESIGN_SAMPLE, only), [0, 32, 100])

    def test_missing_metric_scores_as_worst(self):
        rows = copy.deepcopy(DESIGN_SAMPLE)
        rows[2]["base_shear_kips"] = None
        n = dashboard.normalized(rows)
        self.assertEqual(n[2]["seismic"], 0.0)
        self.assertEqual(n[1]["seismic"], 1.0)  # steel is now the lowest known

    def test_equal_values_all_score_one(self):
        rows = [metrics(10, 5, 5, 5), metrics(10, 5, 5, 5)]
        self.assertEqual(dashboard.fit_scores(rows, WEIGHTS), [100, 100])


class TestSchedule(unittest.TestCase):
    def test_reference_plate(self):
        s = dashboard.schedule_weeks("steel", 85.0, 20000, 8)
        self.assertAlmostEqual(s["foundations"], 7.05)
        self.assertAlmostEqual(s["procurement_wait"], 6.95)
        self.assertAlmostEqual(s["superstructure"], 8.0)
        self.assertAlmostEqual(s["total"], 22.0)

    def test_larger_plate(self):
        s = dashboard.schedule_weeks("steel", 85.0, 80000, 8)
        self.assertAlmostEqual(s["foundations"], 14.1)
        self.assertEqual(s["procurement_wait"], 0.0)
        self.assertAlmostEqual(s["floor_cycle"], 2.2974, places=4)
        self.assertAlmostEqual(s["total"], 32.479, places=3)


class TestHazard(unittest.TestCase):
    def test_from_pad(self):
        hz, notes = dashboard._hazard(STATE, dashboard._config("dashboard.json"))
        self.assertEqual((hz["sds"], hz["sd1"], hz["ie"], hz["risk_category"]), (1.0, 0.6, 1.0, "II"))
        self.assertEqual(hz["source"], "site hazard lookup")
        self.assertEqual(hz["sdc"], "D")
        self.assertEqual(notes, [])

    def test_placeholder_when_missing(self):
        state = {k: v for k, v in STATE.items() if k not in ("sds", "sd1", "ie", "bones")}
        hz, notes = dashboard._hazard(state, dashboard._config("dashboard.json"))
        self.assertEqual(hz["source"], "placeholder")
        self.assertEqual(hz["ie"], 1.0)  # from Risk Category II
        self.assertTrue(notes and "placeholder" in notes[0])


@unittest.skipUnless(HAS_DB, "bay study DB not present")
class TestBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = dashboard.build(copy.deepcopy(STATE))

    def test_three_options_in_order(self):
        self.assertEqual([o["key"] for o in self.data["options"]], ["concrete", "steel", "mass_timber"])
        self.assertEqual(self.data["project"]["stories"], 3)
        self.assertAlmostEqual(self.data["project"]["gross_area_sf"], 120 * 90 * 3)

    def test_lateral_uses_each_schemes_gravity_weight(self):
        for o in self.data["options"]:
            with self.subTest(option=o["key"]):
                lat = o["lateral"]
                self.assertNotIn("error", lat)
                self.assertAlmostEqual(lat["W_kips"], o["basis"]["seismic_weight_kips"], places=3)
                self.assertGreater(lat["V_kips"], 0)
                self.assertAlmostEqual(lat["V_kips"], lat["cs"] * lat["W_kips"], places=3)

    def test_cost_is_gravity_plus_lateral_with_range(self):
        for o in self.data["options"]:
            m, b = o["metrics"], o["basis"]
            with self.subTest(option=o["key"]):
                self.assertAlmostEqual(m["cost_usd"], b["gravity_cost_usd"] + b["lateral_cost_usd"])
                self.assertGreater(b["lateral_cost_usd"], 0)
                self.assertLess(m["cost_lo_usd"], m["cost_usd"])
                self.assertGreater(m["cost_hi_usd"], m["cost_usd"])

    def test_default_scores_match_formula(self):
        opts = self.data["options"]
        expected = dashboard.fit_scores([o["metrics"] for o in opts], self.data["weights"])
        self.assertEqual([o["default_score"] for o in opts], expected)

    def test_concrete_names_the_optimizers_system(self):
        concrete = self.data["options"][0]
        self.assertTrue(concrete["system"].endswith("with special reinforced concrete shear walls"))
        self.assertNotIn("Concrete system:", concrete["system"])


if __name__ == "__main__":
    unittest.main()
