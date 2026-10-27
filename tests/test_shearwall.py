"""Shear walls (lateral_system "shear_wall") against the ShearWall dashboard itself.

Run from the repo root:  python -m unittest discover -s tests -t . -v

tests/fixtures/shearwall_projects.json holds three projects:
    A  the Bones fixture building as shear walls (8 levels, load areas on every level)
    B  a notched plan with a C-core, a corner core, two walls end to end with their joint released,
       thickness changing up the height, load areas on level 2 only, S1 and TL entered
    C  B with flexure only, I_eff = 0.5 I_g, f'c = 6 ksi, a typed Cs, the joint monolithic, and the drift
       check at the center of mass on the "4 stories or less" row
tests/fixtures/shearwall_reference.json.gz is what "SEISMIC ELF - Rigid - Drift - ShearWall.html"
computes for them, read from the page's window.ELF hook (compute, rigidProps, wallStiff, hdGeometry,
solveBeam, chordScan, collectorCalc, veGov, fullSolve, driftCheck). The engine must match it to 1e-9.
"""

import gzip
import json
import math
import unittest
from copy import deepcopy
from pathlib import Path

import elf_engine as E

FIX = Path(__file__).resolve().parent / "fixtures"
PROJECTS = json.loads((FIX / "shearwall_projects.json").read_text(encoding="utf-8"))
with gzip.open(FIX / "shearwall_reference.json.gz", "rt", encoding="utf-8") as f:
    REF = json.load(f)


def diff(ref, got, path="", out=None, tol=1e-9):
    """Mismatches between a reference value (JSON from the page: NaN and Infinity are null) and the engine's.
    Dicts compare only the keys the reference has."""
    out = [] if out is None else out
    if isinstance(ref, dict):
        if not isinstance(got, dict):
            out.append(f"{path}: expected a dict, got {got!r}")
            return out
        for k, v in ref.items():
            if k not in got:
                if v is not None:  # the page writes null where the engine leaves the key out
                    out.append(f"{path}.{k}: missing")
            else:
                diff(v, got[k], f"{path}.{k}", out, tol)
    elif isinstance(ref, list):
        if not isinstance(got, (list, tuple)) or len(got) != len(ref):
            out.append(f"{path}: expected {len(ref)} items, got {got!r}"[:300])
            return out
        for i, (a, b) in enumerate(zip(ref, got)):
            diff(a, b, f"{path}[{i}]", out, tol)
    elif ref is None:
        if not (got is None or (isinstance(got, float) and not math.isfinite(got))):
            out.append(f"{path}: expected null (NaN), got {got!r}")
    elif isinstance(ref, str):
        if ref.replace("−", "-") != got.replace("−", "-"):  # the API writes "-5%" with an ASCII minus
            out.append(f"{path}: expected {ref!r}, got {got!r}")
    elif isinstance(ref, bool):
        if ref != got:
            out.append(f"{path}: expected {ref!r}, got {got!r}")
    elif isinstance(ref, (int, float)):
        if not E.ok(got) or abs(ref - got) > tol * max(1.0, abs(ref)):
            out.append(f"{path}: expected {ref!r}, got {got!r}")
    return out


class Reference:
    """Builds, for one project, the same quantities the page export holds."""

    def __init__(self, key):
        self.key, self.ref = key, REF[key]
        self.eng = E.Engine(PROJECTS[key])

    def compute(self):
        return self.eng.compute()

    def stiff(self):
        out = []
        for i in range(1, self.eng.n + 1):
            P = self.eng.rigid_props(i)
            walls = []
            for j in range(len(self.eng.lfrs)):
                w = self.eng.wall_stiff(j, i)
                walls.append({**w, "share": w.get("share"), "core": w["core"]["name"] if w.get("core") else None})
            out.append({**P, "walls": walls})
        return out

    def beam(self, key):
        dir_, lv, m, a, Fk = key.split("|")
        lv, a = int(lv), int(a)
        G = self.eng.hd_geometry(dir_, lv)
        row = self.eng.compute()["rows"][lv - 1]
        B = self.eng.solve_beam(G, row["fpx"] if Fk == "Fpx" else row["F"], m, a, lv)
        if "err" in B:
            return B
        return {**B, "chord": self.eng.chord_scan(G, B)["max"]}

    def collectors(self, key):
        dir_, lv, m, a = key.split("|")
        lv, a = int(lv), int(a)
        G = self.eng.hd_geometry(dir_, lv)
        B = self.eng.solve_beam(G, self.eng.compute()["rows"][lv - 1]["fpx"], m, a, lv)
        return [self.eng.collector_calc(G, B, gi) for gi in range(len(G["groups"]))]

    def ve(self, key):
        j, m, a = key.split("|")
        return self.eng.ve_gov(int(j), m, a)

    def full(self, key):
        dir_, a, sp = key.split("|")
        B = self.eng.full_solve(dir_, int(a), sp == "true")
        if "err" in B:
            return B
        return {"story": B["story"][1:], "W": [B["W"][j] for j in range(len(self.eng.lfrs))]}

    def drift(self):
        D = self.eng.drift_check()
        return {"err": D["err"]} if "err" in D else D


def errors_only(ref):
    """Errors from the page come back as {'err': text}; the engine returns the same text."""
    return ref


class TestShearWallsAgainstDashboard(unittest.TestCase):
    def check(self, ref, got, path):
        bad = diff(ref, got, path)
        self.assertFalse(bad, f"{len(bad)} mismatches, first: " + "; ".join(bad[:8]))

    def run_project(self, key):
        X = Reference(key)
        r = X.ref
        self.assertIsInstance(X.eng, E.ShearWallEngine)
        # the period note differs only in typography (the page writes Tₐ), so its text is not compared
        ref = {**r["compute"], "P": {k: v for k, v in r["compute"]["P"].items() if k != "note"}}
        self.check(ref, X.compute(), f"{key}.compute")
        self.check(r["stiff"], X.stiff(), f"{key}.stiff")
        for k, v in r["geo"].items():
            dir_, lv = k.split("|")
            self.check(v, X.eng.hd_geometry(dir_, int(lv)), f"{key}.geo[{k}]")
        for k, v in r["beams"].items():
            v = deepcopy(v)
            # Ties are decided by rounding, so they are not compared: a moment peak at a support is both the last sample
            # of the span (which carries the depth d) and the support point (which doesn't); a peak of rounding noise
            # (|M| ~ 1e-13 where the diagram closes) has no meaningful location; and in a symmetric plan the largest unit
            # shear occurs at both ends.
            for m in ("Mpos", "Mneg"):
                if m in v:
                    v[m] = {"M": v[m]["M"]} if abs(v[m]["M"]) < 1e-6 else {"M": v[m]["M"], "s": v[m]["s"]}
            if "vmax" in v:
                v["vmax"] = {"v": v["vmax"]["v"]}
            self.check(v, X.beam(k), f"{key}.beams[{k}]")
        for k, v in r["col"].items():
            self.check(v, X.collectors(k), f"{key}.col[{k}]")
        for k, v in r["ve"].items():
            self.check(v, X.ve(k), f"{key}.ve[{k}]")
        for k, v in r["full"].items():
            self.check(v, X.full(k), f"{key}.full[{k}]")
        self.check(r["drift"], X.drift(), f"{key}.drift")

    def test_project_a_bones_building(self):
        self.run_project("A")

    def test_project_b_cores_and_flexible_methods(self):
        self.run_project("B")

    def test_project_c_flexure_only_typed_cs(self):
        self.run_project("C")

    def test_reference_covers_every_module(self):
        """Guard against a thin fixture: every method, both directions, cores and drift are in it."""
        b = REF["B"]
        self.assertEqual({k.split("|")[2] for k in b["beams"]}, {"rigid", "rigidT", "simple", "cont"})
        self.assertTrue(any(w["core"] for s in b["stiff"] for w in s["walls"]))
        self.assertIn("rows", b["drift"])
        self.assertTrue(all("err" not in v for v in b["ve"].values()))


class TestShearWallAnalyze(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = {k: E.analyze(p) for k, p in PROJECTS.items()}

    def test_json_safe(self):
        for r in self.res.values():
            json.dumps(r, allow_nan=False)

    def test_cs_from_r(self):
        b = self.res["B"]
        # SDS = 0.8, R = 5, Ie = 1.25: Eq. 12.8-2 gives 0.8 / 4 = 0.2, under the 12.8-3 cap and over both floors
        self.assertAlmostEqual(b["base_shear"]["cs"], 0.2, places=12)
        self.assertEqual(b["seismic_coefficient"]["governs"], "12.8-2")
        self.assertEqual(self.res["C"]["inputs"]["cs_source"], "input")
        self.assertTrue(any("typed Cs" in w for w in self.res["C"]["warnings"]))

    def test_rigid_distribution_adds_up(self):
        for r in self.res.values():
            for d in ("X", "Y"):
                for e in r["horizontal_distribution"][d]:
                    self.assertNotIn("error", e)
                    self.assertAlmostEqual(e["sum_R"], e["F"], delta=1e-9 * e["F"])
                    self.assertAlmostEqual(sum(w["force"] for ln in e["lines"] for w in ln["walls"]), e["F"],
                                           delta=1e-9 * e["F"])

    def test_every_collector_closes(self):
        for r in self.res.values():
            for lv in r["collectors"]["levels"]:
                self.assertNotIn("error", lv)
                self.assertTrue(all(x["closes"] for x in lv["lines"]), lv["level"])

    def test_wall_moments_check(self):
        for r in self.res.values():
            for w in r["wall_forces"]["walls"]:
                self.assertNotIn("error", w)
                self.assertTrue(w["checks"]["moment"], w["label"])

    def test_core_segments_share_the_core_moment(self):
        walls = {w["label"]: w for w in self.res["B"]["wall_forces"]["walls"]}
        self.assertEqual(walls["F1"]["core"], "C1")
        self.assertAlmostEqual(walls["F1"]["base_moment"], walls["F2"]["base_moment"], places=6)
        self.assertIsNone(walls["F1"]["T_C_base"])

    def test_released_joint(self):
        joints = {j["key"]: j for j in self.res["B"]["stiffness"]["joints"]}
        self.assertFalse(joints["40,0"]["monolithic"])
        self.assertIsNone(joints["40,0"]["core"])
        self.assertEqual({j["key"]: j for j in self.res["C"]["stiffness"]["joints"]}["40,0"]["core"], "C2")

    def test_flexible_methods(self):
        p = PROJECTS["B"]
        for m in ("simple", "cont"):
            r = E.analyze(p, hd_method=m, col_method=m, ch_method=m, ve_method=m)
            json.dumps(r, allow_nan=False)
            for e in r["horizontal_distribution"]["Y"]:
                self.assertAlmostEqual(e["sum_R"], e["F"], delta=1e-6 * e["F"])
            self.assertTrue(all(x["closes"] for lv in r["collectors"]["levels"] for x in lv["lines"]))

    def test_drift(self):
        d = self.res["B"]["drift"]
        self.assertEqual(d["coefficient"], 0.015)  # Risk Category III, all other structures
        c = self.res["C"]["drift"]
        self.assertEqual(c["coefficient"], 0.015)  # Risk Category IV, 4 stories or less
        self.assertEqual(c["at"], "cm")

    def test_braced_frames_unchanged_by_default(self):
        p = deepcopy(PROJECTS["A"])
        del p["lateral_system"]
        self.assertIsInstance(E.Engine(p), E.BracedFrameEngine)


try:
    from fastapi.testclient import TestClient

    import api
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi and httpx are not installed")
class TestShearWallApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(api.app)

    def post(self, project, **body):
        r = self.client.post("/analyze", json={"project": project, **body})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_project_file_matches_engine(self):
        body = self.post(PROJECTS["B"])
        self.assertEqual(body["lateral_system"], "shear_wall")
        self.assertEqual(body, json.loads(json.dumps(E.analyze(PROJECTS["B"]))))

    def test_request_fields_override_the_project(self):
        p = deepcopy(PROJECTS["B"])
        body = self.post(p, shear_walls={"R": 4, "Cd": 4, "Omega0": 3, "fc_ksi": 6, "drift_at": "cm"},
                         options={"hd_method": "simple", "ve_acc": 1})
        self.assertAlmostEqual(body["base_shear"]["cs"], 0.8 / (4 / 1.25), places=12)
        self.assertEqual(body["inputs"]["Cd"], 4)
        self.assertEqual(body["collectors"]["omega0"], 3)
        self.assertEqual(body["stiffness"]["fc"], 6)
        self.assertEqual(body["drift"]["at"], "cm")
        self.assertEqual(body["horizontal_distribution"]["method"], "simple")
        self.assertEqual(body["wall_forces"]["accidental_torsion"], "+5%")

    def test_shear_walls_block_switches_the_system(self):
        p = deepcopy(PROJECTS["A"])
        del p["lateral_system"]
        self.assertEqual(self.post(p, shear_walls={"R": 5})["lateral_system"], "shear_wall")
        self.assertNotIn("lateral_system", self.post(p))  # braced frames, the teammate's result shape

    def test_risk_category_from_the_bones_hazard_panel(self):
        p = deepcopy(PROJECTS["A"])
        p.pop("rc")
        p["bones"] = {"hazard": {"query": {"risk_category": "III"}}}
        self.assertEqual(self.post(p)["inputs"]["risk_category"], "III")

    def test_thickness_from_the_drawing_pad(self):
        """The pad saves t and ts on each wall; a wall without either takes the default thickness."""
        p = deepcopy(PROJECTS["B"])
        p["plan"]["lfrs"][3].pop("t")
        p["plan"]["lfrs"][3].pop("ts")
        body = self.post(p, shear_walls={"default_thickness_in": 9})
        self.assertEqual(body["plan"]["walls"][3]["t_by_story"], [9, 9, 9, 9])

    def test_diagonal_wall_is_422(self):
        p = deepcopy(PROJECTS["B"])
        p["plan"]["lfrs"][0].update(x2=65)
        self.assertEqual(self.client.post("/analyze", json={"project": p}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
