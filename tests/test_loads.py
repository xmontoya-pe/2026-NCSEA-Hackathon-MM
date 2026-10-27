"""Tests for gravity.loads (spec 6, prompt 4): zones, bay loads, LL reduction."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from gravity.loads import (
    FLOOR,
    ROOF,
    SLAB_ON_GRADE,
    bay_loads,
    kll,
    level_roles,
    no_column_zones,
    reduced_live_load,
    resolve_zones,
    roof_live_load,
)
from gravity.models import GravityInput

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def zone_map_for(input_dict: dict):
    return resolve_zones(GravityInput.model_validate(input_dict))


class TestLevelsAndZones(unittest.TestCase):
    def setUp(self):
        self.zm = zone_map_for(fixture("simple_rect.json"))

    def test_level_roles(self):
        roles = level_roles(GravityInput.model_validate(fixture("simple_rect.json")))
        self.assertEqual(roles, {"L1": SLAB_ON_GRADE, "L2": FLOOR, "L3": FLOOR, "ROOF": ROOF})

    def test_typical_zones_copy_to_other_framed_floors_only(self):
        l2 = self.zm.levels["L2"].zones
        l3 = self.zm.levels["L3"].zones
        self.assertEqual({(z.zone_id, z.copied_from) for z in l2}, {("z1", None), ("z2", None)})
        self.assertEqual({(z.zone_id, z.copied_from) for z in l3}, {("z1", "L2"), ("z2", "L2")})

    def test_slab_on_grade_has_no_load_zones(self):
        self.assertEqual(self.zm.levels["L1"].zones, [])

    def test_roof_is_roof_occupancy_over_whole_plate(self):
        (roof,) = self.zm.levels["ROOF"].zones
        self.assertEqual(roof.occupancy.key, "roof")
        self.assertAlmostEqual(roof.geometry.area, 150 * 90)

    def test_snow_defaults_to_zero_with_warning(self):
        self.assertEqual(self.zm.snow_psf, 0.0)
        self.assertTrue(any("snow" in w for w in self.zm.warnings))


class TestBayLoads(unittest.TestCase):
    def setUp(self):
        self.zm = zone_map_for(fixture("simple_rect.json"))

    def test_bay_straddling_two_zones(self):
        # L2 bay x 105-135: half office (z1, 0-120), half corridor (z2, 120-150).
        # Lookup LL: office 50 + 15 partitions = 65; corridor 80 + 0 = 80 -> corridor governs.
        b = bay_loads(self.zm, "L2", rect(105, 0, 135, 30))
        self.assertEqual(b.governing_occupancy, "corridor_above_first")
        self.assertEqual((b.ll_psf, b.partitions_psf, b.lookup_ll_psf), (80, 0, 80))
        self.assertEqual(b.area_fractions, {"corridor_above_first": 0.5, "office": 0.5})
        self.assertEqual(b.zone_ids, ["z1", "z2"])
        # The office half still needs its walking criterion (most stringent wins).
        self.assertEqual(b.criterion, "Walking b=0.03")
        self.assertTrue(b.ll_reducible)
        self.assertEqual(b.sdl_psf, 15.0)

    def test_office_bay_on_copied_floor(self):
        b = bay_loads(self.zm, "L3", rect(0, 0, 30, 30))
        self.assertEqual(b.governing_occupancy, "office")
        self.assertEqual((b.ll_psf, b.partitions_psf, b.lookup_ll_psf), (50, 15, 65))
        self.assertEqual(b.criterion, "Walking b=0.03")
        self.assertEqual(b.zone_ids, ["z1"])

    def test_roof_bay(self):
        b = bay_loads(self.zm, "ROOF", rect(0, 0, 30, 30))
        self.assertEqual(b.governing_occupancy, "roof")
        self.assertEqual(b.lookup_ll_psf, 20)  # max(Lr 20, snow 0)
        self.assertIsNone(b.criterion)

    def test_slab_on_grade_bay_rejected(self):
        with self.assertRaises(ValueError):
            bay_loads(self.zm, "L1", rect(0, 0, 30, 30))


class TestNoColumnZones(unittest.TestCase):
    def test_no_column_zone_on_slab_on_grade_level(self):
        # no_column_zone.json: z2 (assembly, no_columns) is on L1, the slab-on-grade level.
        # It keeps the L1 story column-free but puts no load on any framed floor.
        zm = zone_map_for(fixture("no_column_zone.json"))
        self.assertEqual([z.zone_id for z in no_column_zones(zm, "L1")], ["z2"])
        for level in ("L2", "L3", "ROOF"):
            self.assertEqual(no_column_zones(zm, level), [])
        b = bay_loads(zm, "L2", rect(30, 0, 60, 30))
        self.assertEqual(b.governing_occupancy, "office")

    def test_no_column_zone_on_framed_floor(self):
        # Same zone moved to L2: its assembly load applies to the L2 floor and its
        # no_columns flag to the L2 story only. The L3 copy carries the occupancy only.
        inp = fixture("no_column_zone.json")
        inp["zones"][1]["level"] = "L2"
        zm = zone_map_for(inp)
        self.assertEqual([z.zone_id for z in no_column_zones(zm, "L2")], ["z2"])
        self.assertEqual(no_column_zones(zm, "L3"), [])
        b = bay_loads(zm, "L2", rect(40, 20, 80, 70))
        self.assertEqual(b.governing_occupancy, "assembly_movable_seats")
        self.assertEqual(b.lookup_ll_psf, 100)
        self.assertFalse(b.ll_reducible)
        self.assertEqual(bay_loads(zm, "L3", rect(40, 20, 80, 70)).governing_occupancy,
                         "assembly_movable_seats")


class TestOverridesAndCoverage(unittest.TestCase):
    def test_explicit_zone_overrides_copy_only_where_it_overlaps(self):
        inp = fixture("simple_rect.json")
        inp["zones"].append({"id": "z4", "level": "L3", "occupancy": "storage_light",
                             "polygon": rect(0, 0, 30, 30)})
        zm = zone_map_for(inp)
        # On L3, z4 replaces the office copy inside 0-30 x 0-30 only.
        b = bay_loads(zm, "L3", rect(0, 0, 30, 30))
        self.assertEqual(b.area_fractions, {"storage_light": 1.0})
        self.assertEqual(bay_loads(zm, "L3", rect(30, 0, 60, 30)).governing_occupancy, "office")
        # z4 copied to L2 is clipped away by L2's own explicit z1.
        self.assertEqual(bay_loads(zm, "L2", rect(0, 0, 30, 30)).area_fractions,
                         {"office": 1.0})

    def test_uncovered_area_gets_default_with_warning(self):
        inp = fixture("simple_rect.json")
        inp["zones"] = [z for z in inp["zones"] if z["id"] != "z2"]  # leave 120-150 bare
        zm = zone_map_for(inp)
        # 30 ft x 90 ft = 2,700 sq ft uncovered on each framed floor.
        self.assertTrue(any("Level L2: 2,700 sq ft" in w for w in zm.warnings))
        b = bay_loads(zm, "L2", rect(120, 0, 150, 30))
        self.assertEqual(b.zone_ids, ["default"])
        self.assertEqual(b.governing_occupancy, "office")

    def test_unknown_occupancy_warns_and_uses_default(self):
        inp = fixture("simple_rect.json")
        inp["zones"][0]["occupancy"] = "bowling_alley"
        zm = zone_map_for(inp)
        self.assertTrue(any("bowling_alley" in w for w in zm.warnings))
        self.assertEqual(bay_loads(zm, "L2", rect(0, 0, 30, 30)).governing_occupancy, "office")

    def test_zone_outside_outline_ignored_with_warning(self):
        inp = fixture("simple_rect.json")
        inp["zones"].append({"id": "z9", "level": "L2", "occupancy": "storage_light",
                             "polygon": rect(200, 200, 230, 230)})
        zm = zone_map_for(inp)
        self.assertTrue(any("Zone z9" in w for w in zm.warnings))

    def test_typical_zones_off(self):
        inp = copy.deepcopy(fixture("simple_rect.json"))
        inp["typical_zones_apply_to_all_levels"] = False
        zm = zone_map_for(inp)
        self.assertEqual([z.zone_id for z in zm.levels["L3"].zones], ["default"])


class TestLiveLoadReduction(unittest.TestCase):
    def test_interior_column_one_floor(self):
        # KLL*AT = 4*900 = 3600; 0.25 + 15/60 = 0.50; limit 0.5 -> L = 50*0.5 = 25.0 psf
        r = reduced_live_load(50, 4, 900, floors_supported=1)
        self.assertAlmostEqual(r.l_psf, 25.0)
        self.assertIn("3600", r.note)

    def test_interior_column_two_floors(self):
        # KLL*AT = 4*1800 = 7200; 0.25 + 15/84.853 = 0.4268 (> 0.4) -> L = 21.34 psf
        r = reduced_live_load(50, 4, 1800, floors_supported=2)
        self.assertAlmostEqual(r.factor, 0.426777, places=5)
        self.assertAlmostEqual(r.l_psf, 21.339, places=3)

    def test_edge_column_with_spec_kll(self):
        # KLL*AT = 2*450 = 900; 0.25 + 15/30 = 0.75 -> L = 37.5 psf
        r = reduced_live_load(50, kll("edge_column"), 450, floors_supported=1)
        self.assertAlmostEqual(r.l_psf, 37.5)

    def test_limits(self):
        # One floor, very large AT: 0.25 + 15/200 = 0.325 -> limited to 0.5.
        self.assertAlmostEqual(reduced_live_load(50, 4, 10000, 1).factor, 0.5)
        # KLL*AT < 400: no reduction.
        self.assertAlmostEqual(reduced_live_load(50, 2, 150, 1).l_psf, 50)
        # Not reducible, or Lo > 100: no reduction.
        self.assertAlmostEqual(reduced_live_load(100, 4, 3600, 1, reducible=False).l_psf, 100)
        self.assertAlmostEqual(reduced_live_load(125, 4, 3600, 1).l_psf, 125)

    def test_kll_from_config(self):
        self.assertEqual(kll("interior_column"), 4)
        self.assertEqual(kll("edge_column"), 2)
        self.assertEqual(kll("corner_column"), 4)  # ASCE 7-22 Table 4.7-1, no cantilever

    def test_corner_column_with_asce_kll(self):
        # KLL*AT = 4*225 = 900; 0.25 + 15/30 = 0.75 -> L = 37.5 psf
        r = reduced_live_load(50, kll("corner_column"), 225, floors_supported=1)
        self.assertAlmostEqual(r.l_psf, 37.5)


class TestRoofLiveLoad(unittest.TestCase):
    def test_r1_ranges(self):
        self.assertAlmostEqual(roof_live_load(20, 150).l_psf, 20.0)  # At <= 200: R1 = 1
        self.assertAlmostEqual(roof_live_load(20, 400).l_psf, 16.0)  # R1 = 1.2 - 0.4 = 0.8
        self.assertAlmostEqual(roof_live_load(20, 900).l_psf, 12.0)  # At >= 600: R1 = 0.6

    def test_non_ordinary_roof_not_reduced(self):
        self.assertAlmostEqual(roof_live_load(30, 900).l_psf, 30.0)


if __name__ == "__main__":
    unittest.main()
