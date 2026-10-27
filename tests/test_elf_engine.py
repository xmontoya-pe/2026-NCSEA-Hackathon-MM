"""Checks for elf_engine.py and api.py.

Run from the repo root:  python -m unittest discover -s tests -t . -v
The engine tests need only the standard library; the API tests skip unless the packages in
requirements-api.txt are installed.

Expected values are hand calculations for bones-gravity-inputs.json with 55 psf self-weight:
equal brace areas and 30 ft bays give equal frame stiffness, so CR = (60, 45) and J = 11,250k.
Y loading at +5% (e = 0.05 x 120 = 6 ft): Y1 = 0.468F, Y2 = 0.532F, X1 = +0.024F, X2 = -0.024F.
"""

import json
import unittest
from copy import deepcopy
from pathlib import Path

import elf_engine as E
from bones_to_elf import convert

ROOT = Path(__file__).resolve().parent.parent
BONES = json.loads((ROOT / "tests" / "fixtures" / "bones-gravity-inputs.json").read_text())
SDS, SD1 = 0.30, 0.12  # SDC B


def frame(res, label):
    return next(f for f in res["frame_forces"]["frames"] if f["label"] == label)


def hd_frames(entry):
    """Module 9 frame forces at one level, by label: parallel frames' force, perpendicular frames' torsional force."""
    out = {f["label"]: f["force"] for line in entry["lines"] for f in line["frames"]}
    out.update({f["label"]: f["torsional"] for f in entry["perpendicular"]})
    return out


class TestEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project, cls.summary = convert(BONES, 55, sds=SDS, sd1=SD1)
        cls.res = E.analyze(cls.project)
        cls.eng = E.Engine(cls.project)

    def test_json_safe(self):
        json.dumps(self.res, allow_nan=False)

    def test_weight_matches_converter(self):
        self.assertTrue(all(L["weight_from"] == "load areas" for L in self.res["levels"]))
        self.assertAlmostEqual(self.res["base_shear"]["W"], self.summary["W"], places=6)
        self.assertAlmostEqual(self.res["base_shear"]["W"], 7352, delta=5)  # hand calculation

    def test_period(self):
        P = self.res["period"]
        self.assertAlmostEqual(P["Ta"], 0.02 * 106 ** 0.75, places=9)
        self.assertAlmostEqual(P["Ta"], 0.661, places=3)
        self.assertEqual(P["T"], P["Ta"])
        self.assertAlmostEqual(P["k"], 1 + (P["Ta"] - 0.5) / 2, places=9)

    def test_base_shear_and_vertical_distribution(self):
        B = self.res["base_shear"]
        self.assertEqual(self.res["inputs"]["cs_source"], "input")
        self.assertAlmostEqual(B["cs"], 0.0605, places=4)
        self.assertAlmostEqual(B["V"], B["cs"] * B["W"], places=6)
        self.assertAlmostEqual(B["sum_Cvx"], 1.0, places=9)
        self.assertAlmostEqual(B["sum_F"], B["V"], places=6)
        self.assertAlmostEqual(self.res["levels"][0]["Vx"], B["V"], places=6)

    def test_diaphragm_forces_within_limits(self):
        for L in self.res["levels"]:
            d = L["diaphragm"]
            self.assertLessEqual(d["Fpx_min"] - 1e-9, d["Fpx"])
            self.assertLessEqual(d["Fpx"], d["Fpx_max"] + 1e-9)

    def test_center_of_rigidity(self):
        for s in self.res["stiffness"]:
            k = s["frames"][0]["k"]
            self.assertTrue(all(abs(f["k"] - k) < 1e-9 * k for f in s["frames"]))
            self.assertAlmostEqual(s["cr"][0], 60, places=9)
            self.assertAlmostEqual(s["cr"][1], 45, places=9)
            self.assertAlmostEqual(s["J"], 11250 * k, delta=1e-9 * 11250 * k)

    def test_center_of_mass(self):
        typ = self.res["levels"][0]["cm"]
        self.assertAlmostEqual(typ[0], 60, places=6)
        self.assertAlmostEqual(typ[1] - 45, 0.146, delta=0.005)  # heavier office bays on the north row
        roof = self.res["levels"][-1]["cm"]
        self.assertAlmostEqual(roof[0], 60, places=6)
        self.assertAlmostEqual(roof[1], 45, places=6)

    def test_rigid_y_plus_5(self):
        for entry in self.res["horizontal_distribution"]["Y"]:
            self.assertNotIn("error", entry)
            F, f = entry["F"], hd_frames(entry)
            self.assertAlmostEqual(f["Y1"] / F, 0.468, places=6)
            self.assertAlmostEqual(f["Y2"] / F, 0.532, places=6)
            self.assertAlmostEqual(f["X1"] / F, 0.024, places=6)
            self.assertAlmostEqual(f["X2"] / F, -0.024, places=6)
            self.assertAlmostEqual(entry["sum_R"], F, places=6)

    def test_rigid_y_minus_5_swaps(self):
        entry = E.clean(self.eng.hd_level("Y", 1, "Fx", -1))
        F, f = entry["F"], hd_frames(entry)
        self.assertAlmostEqual(f["Y1"] / F, 0.532, places=6)
        self.assertAlmostEqual(f["Y2"] / F, 0.468, places=6)

    def test_frame_force_envelope(self):
        V = self.res["base_shear"]["V"]
        y1, y2 = frame(self.res, "Y1"), frame(self.res, "Y2")
        self.assertAlmostEqual(y1["base_shear"], 0.532 * V, places=6)
        self.assertAlmostEqual(y2["base_shear"], 0.532 * V, places=6)
        self.assertEqual(y1["accidental_torsion"], "-5%")
        self.assertEqual(y2["accidental_torsion"], "+5%")

    def test_frame_statics(self):
        for f in self.res["frame_forces"]["frames"]:
            self.assertNotIn("error", f)
            self.assertTrue(f["checks"]["statics"], f["label"])
            self.assertTrue(f["checks"]["moment"], f["label"])

    def test_two_bay_x_and_alternating_braces(self):
        p = deepcopy(self.project)
        for l in p["plan"]["lfrs"]:
            if l["label"] == "X1":
                l.update(type="x2", L1=15)
            if l["label"] == "Y1":
                l["alt"] = True
        res = E.analyze(p)
        x1 = frame(res, "X1")
        self.assertTrue(x1["checks"]["statics"])
        self.assertTrue(x1["checks"]["moment"])
        self.assertEqual(len(x1["stories"][0]["braces"]), 2)
        self.assertEqual(len(x1["stories"][0]["column_axial"]), 3)
        self.assertTrue(frame(res, "Y1")["checks"]["statics"])

    def test_cs_computed_when_blank(self):
        p = deepcopy(self.project)
        p["cs"] = None
        res = E.analyze(p, R=3.0)
        self.assertTrue(res["inputs"]["cs_source"].startswith("computed"))
        Ta = res["period"]["T"]
        self.assertAlmostEqual(res["base_shear"]["cs"], E.cs_of(SDS, SD1, Ta, 1.0, 3.0), places=12)

    def test_no_hazard_values(self):
        p, _ = convert(BONES, 55)
        res = E.analyze(p)
        json.dumps(res, allow_nan=False)
        self.assertIsNone(res["base_shear"]["V"])
        self.assertTrue(any("Enter a positive Cs" in w for w in res["warnings"]))
        self.assertTrue(all("error" in f for f in res["frame_forces"]["frames"]))
        self.assertIsNotNone(res["base_shear"]["W"])

    def test_sdc_d_rejected(self):
        with self.assertRaises(ValueError):
            convert(BONES, 55, sds=0.5, sd1=0.1)

    def test_non_orthogonal_outline_rejected(self):
        p = deepcopy(self.project)
        p["plan"]["verts"] = [[0, 0], [120, 0], [100, 90], [0, 90]]
        with self.assertRaises(ValueError):
            E.Engine(p)


try:
    from fastapi.testclient import TestClient

    import api
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi and httpx are not installed")
class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)

    def test_health(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_analyze_bones(self):
        r = self.client.post("/analyze/bones", json={"bones": BONES, "self_weight_psf": 55, "sds": SDS, "sd1": SD1})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertAlmostEqual(body["base_shear"]["W"], 7352, delta=5)
        self.assertIn("project", body["conversion"])

    def test_analyze_project(self):
        project, _ = convert(BONES, 55, sds=SDS, sd1=SD1)
        r = self.client.post("/analyze", json={"project": project, "options": {"ve_acc": 1, "hd_force": "Fpx"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["frame_forces"]["accidental_torsion"], "+5%")

    def test_analyze_without_hazard_is_json_safe(self):
        project, _ = convert(BONES, 55)
        r = self.client.post("/analyze", json={"project": project})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["base_shear"]["V"])

    def test_sdc_d_is_422(self):
        r = self.client.post("/analyze/bones", json={"bones": BONES, "self_weight_psf": 55, "sds": 0.5, "sd1": 0.1})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
