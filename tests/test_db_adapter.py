"""Tests for gravity.db_adapter against real rows of data/bay_studies.sqlite (spec 6, prompt 3).

Expected values come from the DB rows shown during inspection (spec 6 step 2) and from
config/costs.json; each cost check shows its arithmetic.
"""

from __future__ import annotations

import sqlite3
import unittest

from gravity import db_adapter
from gravity.db_adapter import available_loads, available_spans, framing_options

FLOOR_BEAM_SPANS = [20.0, 22.0, 24.0, 26.0, 28.0, 30.0, 32.0, 34.0, 36.0, 38.0, 40.0, 42.0,
                    44.0, 46.0, 48.0, 50.0]
FLOOR_GIRDER_SPANS = [20.0, 24.0, 27.0, 30.0, 32.0, 36.0, 40.0, 45.0]
WALKING = "Walking b=0.03"


def by_row(options, db_row):
    matches = [o for o in options if o.db_row == db_row]
    assert len(matches) == 1, f"expected one option with db_row {db_row}, got {len(matches)}"
    return matches[0]


@unittest.skipUnless(db_adapter.db_path().is_file(), "bay study DB not present")
class TestFloorLookup(unittest.TestCase):
    def test_bay_1_values_and_cost(self):
        # bay_id 1: STL_COMP 20x20, W12X22 @ 10 ft beams, W14X22 girders, LL=50.
        o = by_row(framing_options("steel", "floor", 20, 20, ll_psf=50, sdl_psf=15), 1)
        self.assertEqual(o.db_system, "STL_COMP")
        self.assertEqual(o.criterion, "Strength LL=50 psf")
        self.assertEqual(o.beam, "W12X22 @ 10ft")
        self.assertEqual(o.girder, "W14X22")
        self.assertEqual(o.slab, '2VLI + 4.5" NW (6.5")')
        self.assertAlmostEqual(o.depth_in, 20.2)
        self.assertAlmostEqual(o.steel_psf, 3.3)
        # self weight = 3.3 steel + 69.0 slab/deck (config/systems.json) = 72.3 psf
        self.assertAlmostEqual(o.self_wt_psf, 72.3)
        self.assertAlmostEqual(o.carbon_kgco2e_per_sf, 7.93)
        self.assertEqual(o.warnings, [])
        # cost, USD/sf:
        #   steel    3.3 / 2000 * 5000                    = 8.2500
        #   deck                                            4.5000
        #   concrete 0.0169753 cy/sf * 325                 = 5.5170
        #   reinf                                           0.7500
        #   studs    (10/(20*10) + 8/(20*20)) * 4 = 0.07*4 = 0.2800
        #   total                                          = 19.2970
        self.assertAlmostEqual(o.cost_breakdown["framing_steel"], 8.25)
        self.assertAlmostEqual(o.cost_breakdown["studs"], 0.28)
        self.assertAlmostEqual(o.cost_per_sf, 19.297, places=3)

    def test_spans_round_up_to_table(self):
        at_20 = {o.db_row for o in framing_options("steel", "floor", 20, 20, 50, 15)}
        at_19 = {o.db_row for o in framing_options("steel", "floor", 19, 19, 50, 15)}
        self.assertTrue(at_20)
        self.assertEqual(at_19, at_20)

    def test_rounded_spans_are_tight_and_cover_the_bay(self):
        # 21 x 25: beams in X round to 22 / girders 27; beams in Y round to 26 / girders 24.
        expected = {"beams_span_x": (22.0, 27.0), "beams_span_y": (26.0, 24.0)}
        for o in framing_options("steel", "floor", 21, 25, 50, 15):
            self.assertEqual((o.beam_span_ft, o.girder_span_ft), expected[o.orientation])

    def test_both_orientations_tried(self):
        xy = {o.db_row for o in framing_options("steel", "floor", 20, 30, 50, 15)}
        yx = {o.db_row for o in framing_options("steel", "floor", 30, 20, 50, 15)}
        self.assertEqual(xy, yx)

    def test_spans_beyond_table_give_no_options(self):
        # Beam spans stop at 50 ft and girder spans at 45 ft, in either orientation.
        self.assertEqual(framing_options("steel", "floor", 52, 46, 50, 15), [])

    def test_ll_rounds_up_to_next_row(self):
        options = framing_options("steel", "floor", 20, 20, ll_psf=65, sdl_psf=15)
        self.assertTrue(options)
        self.assertTrue(all(o.design_ll_psf == 100 for o in options))
        self.assertEqual(by_row(options, 2).criterion, "Strength LL=100 psf")

    def test_ll_beyond_table_gives_no_options(self):
        self.assertEqual(framing_options("steel", "floor", 20, 20, ll_psf=125, sdl_psf=15), [])

    def test_sdl_excess_added_to_ll_with_warning(self):
        # SDL 25 vs design 15: lookup LL = 50 + 10 = 60 -> LL=100 rows (ASSUMPTIONS L-1).
        options = framing_options("steel", "floor", 20, 20, ll_psf=50, sdl_psf=25)
        o = by_row(options, 2)
        self.assertEqual(o.lookup_ll_psf, 60)
        self.assertTrue(any("10 psf excess" in w for w in o.warnings))

    def test_options_sorted_cheapest_first(self):
        costs = [o.cost_per_sf for o in framing_options("steel", "floor", 30, 30, 50, 15)]
        self.assertTrue(costs)
        self.assertEqual(costs, sorted(costs))


@unittest.skipUnless(db_adapter.db_path().is_file(), "bay study DB not present")
class TestVibrationCriterion(unittest.TestCase):
    def test_walking_row_used_directly_when_ll_fits(self):
        # bay_id 3 is 'Walking b=0.03' at 20x20, designed for LL=50.
        options = framing_options("steel", "floor", 20, 20, 50, 15, criterion=WALKING)
        o = by_row(options, 3)
        self.assertEqual(o.criterion, WALKING)
        self.assertEqual(o.warnings, [])

    def test_envelope_when_ll_exceeds_walking_rows(self):
        # Office: 50 + 15 partitions = 65 > walking rows' LL=50 -> envelope with the
        # Strength LL=100 row (decision 2). At 20x20 @ 10 ft both rows are 3.3 psf and
        # 20.2 in deep, so the tie goes to the Strength row, bay_id 2.
        options = framing_options("steel", "floor", 20, 20, 65, 15, criterion=WALKING)
        self.assertTrue(options)
        self.assertTrue(all(any("Envelope" in w for w in o.warnings) for o in options))
        self.assertEqual(by_row(options, 2).spacing_ft, 10.0)

    def test_unknown_criterion_rejected(self):
        with self.assertRaises(ValueError):
            framing_options("steel", "floor", 20, 20, 50, 15, criterion="Walking b=0.99")

    def test_explicit_strength_criterion_matches_default(self):
        default = {o.db_row for o in framing_options("steel", "floor", 20, 20, 50, 15)}
        explicit = {o.db_row for o in framing_options("steel", "floor", 20, 20, 50, 15,
                                                      criterion="Strength")}
        self.assertEqual(explicit, default)


@unittest.skipUnless(db_adapter.db_path().is_file(), "bay study DB not present")
class TestRoofLookup(unittest.TestCase):
    def setUp(self):
        self.options = framing_options("steel", "roof", 20, 20, ll_psf=20, sdl_psf=15)

    def find(self, db_system, beam, girder, slab_prefix):
        matches = [o for o in self.options if o.db_system == db_system and o.beam == beam
                   and o.girder == girder and o.slab.startswith(slab_prefix)]
        self.assertTrue(matches, f"no {db_system} option {beam} / {girder} on {slab_prefix}")
        return matches[0]

    def test_w_beam_roof_row(self):
        # des_steel_roof design 1: W8X10 @ 6.667 on W12X14, 1.5B 20 ga, 2.2 psf steel.
        o = self.find("STL_ROOF_W", "W8X10 @ 6.667ft", "W12X14", "1.5B 20 ga")
        # self weight = 2.2 steel + 2.0 deck (ref_roof_deck_spans 1.5B 20 ga) = 4.2 psf
        self.assertAlmostEqual(o.self_wt_psf, 4.2)
        # cost = 2.2 / 2000 * 5000 + 2.75 deck = 5.50 + 2.75 = 8.25 USD/sf
        self.assertAlmostEqual(o.cost_per_sf, 8.25)

    def test_joist_roof_row_splits_joist_and_girder_steel(self):
        # des_steel_roof design 2: 12KCS1 @ 6.667 on W12X14, 1.6 psf steel.
        o = self.find("STL_ROOF_J", "12KCS1 @ 6.667ft", "W12X14", "1.5B 20 ga")
        # joists  6.0 plf / 6.667 ft = 0.900 psf -> 0.900 / 2000 * 3800 = 1.710
        # girders 1.6 - 0.900       = 0.700 psf -> 0.700 / 2000 * 5000 = 1.750
        # deck                                                         = 2.750
        # total                                                        = 6.210 USD/sf
        self.assertAlmostEqual(o.cost_breakdown["joists"], 1.710, places=2)
        self.assertAlmostEqual(o.cost_breakdown["girders"], 1.750, places=2)
        self.assertAlmostEqual(o.cost_per_sf, 6.21, places=2)

    def test_roof_rejects_vibration_criterion(self):
        with self.assertRaises(ValueError):
            framing_options("steel", "roof", 20, 20, 20, 15, criterion=WALKING)


@unittest.skipUnless(db_adapter.db_path().is_file(), "bay study DB not present")
class TestAvailable(unittest.TestCase):
    def test_floor_spans(self):
        self.assertEqual(available_spans("steel", "floor"), (FLOOR_BEAM_SPANS, FLOOR_GIRDER_SPANS))

    def test_roof_spans(self):
        self.assertEqual(available_spans("steel", "roof"), (FLOOR_BEAM_SPANS, FLOOR_GIRDER_SPANS))

    def test_floor_loads(self):
        self.assertEqual(available_loads("steel", "floor"), ([50.0, 100.0], [15.0]))

    def test_roof_loads(self):
        self.assertEqual(available_loads("steel", "roof"), ([20.0, 30.0, 40.0, 50.0], [15.0]))


@unittest.skipUnless(db_adapter.db_path().is_file(), "bay study DB not present")
class TestSafety(unittest.TestCase):
    def test_db_is_opened_read_only(self):
        with self.assertRaises(sqlite3.OperationalError):
            db_adapter._connect().execute("CREATE TABLE should_fail (x INTEGER)")

    def test_bad_use_rejected(self):
        with self.assertRaises(ValueError):
            framing_options("steel", "basement", 20, 20, 50, 15)

    def test_unmapped_system_rejected(self):
        with self.assertRaises(ValueError):
            framing_options("concrete", "floor", 20, 20, 50, 15)


if __name__ == "__main__":
    unittest.main()
