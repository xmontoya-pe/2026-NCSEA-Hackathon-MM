"""Tests for bones_service/gravity_bridge.py (pad state -> gravity input) and the FastAPI app.

Run from the repo root:  python -m unittest discover -s tests -v
The API tests skip unless fastapi, httpx and the Sketch Reader requirements are installed.
"""

from __future__ import annotations

import copy
import unittest

from bones_service import gravity_bridge as GB
from gravity.models import GravityInput


def pad_state() -> dict:
    """A 90 x 60 ft plate, 3 stories of 13 ft; office on level 1, level 2 left unassigned."""
    rect = {"id": "1", "x1": 0, "y1": 0, "x2": 90, "y2": 60}
    return {
        "project": "Bridge test",
        "levels": [{"name": "", "h": 13, "psf": 150, "la": True},
                   {"name": "", "h": 26, "psf": 150, "la": True},
                   {"name": "", "h": 39, "psf": 150, "la": True}],
        "plan": {"verts": [[0, 0], [90, 0], [90, 60], [0, 60]], "closed": True, "lfrs": [],
                 "lv": [{"la": [dict(rect)], "laIds": [{"id": "1", "psf": 100}], "laActive": "1"}
                        for _ in range(3)]},
        "bones": {"loads": {"1": {"1": {"LL": 50, "SDL": 15, "occupancy": "office"}},
                            "2": {"1": {"LL": 50, "SDL": 15}}},
                  "grids": {"x": [{"label": "A", "pos": 0}, {"label": "B", "pos": 30}],
                            "y": [{"label": "1", "pos": 30}]},
                  "snow": {"pf_psf": 21}},
    }


class TestBridge(unittest.TestCase):
    def setUp(self):
        self.inp, self.notes = GB.to_gravity_input(pad_state())

    def test_valid_contract(self):
        GravityInput.model_validate(self.inp)

    def test_levels(self):
        self.assertEqual(self.inp["levels"], [
            {"name": "L1", "elev_ft": 0.0, "floor_to_floor_ft": 13.0},
            {"name": "L2", "elev_ft": 13.0, "floor_to_floor_ft": 13.0},
            {"name": "L3", "elev_ft": 26.0, "floor_to_floor_ft": 13.0},
            {"name": "ROOF", "elev_ft": 39.0, "floor_to_floor_ft": None},
        ])

    def test_zones_from_occupancy_only(self):
        self.assertEqual(self.inp["zones"], [{"id": "L2-1-1", "level": "L2", "occupancy": "office",
                                              "polygon": [[0.0, 0.0], [90.0, 0.0], [90.0, 60.0], [0.0, 60.0]]}])
        self.assertFalse(self.inp["typical_zones_apply_to_all_levels"])
        self.assertTrue(any("L3 area 1" in n for n in self.notes))   # no occupancy chosen

    def test_notes_on_what_is_not_sent(self):
        self.assertTrue(any("21 psf" in n and "not sent" in n for n in self.notes))
        self.assertTrue(any("SDL" in n for n in self.notes))

    def test_dimension_lines(self):
        self.assertEqual(self.inp["dimension_lines"], {"x_ft": [0.0, 30.0, 90.0], "y_ft": [0.0, 30.0, 60.0]})
        state = pad_state()
        del state["bones"]["grids"]
        inp, _ = GB.to_gravity_input(state)
        self.assertEqual(inp["dimension_lines"], {"x_ft": [0.0, 90.0], "y_ft": [0.0, 60.0]})

    def test_defaults_and_overrides(self):
        self.assertEqual(self.inp["systems"], ["steel"])
        self.assertEqual(self.inp["constraints"], GB.DEFAULT_CONSTRAINTS)
        inp, _ = GB.to_gravity_input(pad_state(), {"constraints": {"max_bay_ft": "40", "max_structure_depth_in": ""}})
        self.assertEqual(inp["constraints"]["max_bay_ft"], 40.0)
        self.assertIsNone(inp["constraints"]["max_structure_depth_in"])

    def test_unknown_occupancy_left_out(self):
        state = pad_state()
        state["bones"]["loads"]["1"]["1"]["occupancy"] = "bowling_alley"
        inp, notes = GB.to_gravity_input(state)
        self.assertEqual(inp["zones"], [])
        self.assertTrue(any("bowling_alley" in n for n in notes))

    def test_rejects_unusable_plans(self):
        for change in (lambda s: s["plan"].update(closed=False),
                       lambda s: s["levels"][1].update(h=10),
                       lambda s: s.update(levels=[]),
                       lambda s: s["plan"]["verts"].pop() and s["plan"]["verts"].pop()):
            state = copy.deepcopy(pad_state())
            change(state)
            with self.subTest(state=state), self.assertRaises(ValueError):
                GB.to_gravity_input(state)

    def test_occupancies_for_dropdown(self):
        keys = {o["key"] for o in GB.occupancies()}
        self.assertIn("office", keys)
        self.assertTrue(any(o["is_roof"] for o in GB.occupancies()))

    def test_run_end_to_end(self):
        res = GB.run(pad_state())
        self.assertEqual(set(res), {"input", "notes", "output"})
        self.assertEqual([s["system"] for s in res["output"]["schemes"]], ["steel"])


try:
    from fastapi.testclient import TestClient

    from bones_service import api
except ImportError:  # fastapi/httpx/pillow not installed
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi, httpx or Sketch Reader requirements not installed")
class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app, raise_server_exceptions=False)

    def test_health_and_occupancies(self):
        self.assertTrue(self.client.get("/api/health").json()["gravity"])
        self.assertIn("office", {o["key"] for o in self.client.get("/api/occupancies").json()["occupancies"]})

    def test_gravity(self):
        r = self.client.post("/api/gravity", json={"state": pad_state(), "options": {"constraints": {"max_bay_ft": 40}}})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["input"]["constraints"]["max_bay_ft"], 40.0)
        self.assertEqual(body["output"]["schemes"][0]["system"], "steel")

    def test_errors_are_plain_messages(self):
        r = self.client.post("/api/gravity", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("drawing pad", r.json()["error"])
        state = pad_state()
        state["plan"]["closed"] = False
        r = self.client.post("/api/gravity", json={"state": state})
        self.assertEqual(r.status_code, 400)
        self.assertIn("outline", r.json()["error"])
        r = self.client.post("/api/gravity", json={"state": pad_state(),
                                                   "options": {"constraints": {"min_bay_ft": 50}}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("min_bay_ft", r.json()["error"])

    def test_page(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
