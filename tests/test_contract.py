"""Contract tests for the stub ``run()`` and CLI (spec section 6, prompt 1).

Run from the repo root:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

import gravity
from gravity.models import GravityInput, GravityOutput

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
FIXTURE_NAMES = ["simple_rect.json", "l_shape.json", "no_column_zone.json"]
SAMPLE_OUTPUT = REPO_ROOT / "examples" / "sample_output.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestFixtures(unittest.TestCase):
    def test_each_fixture_validates_runs_and_output_matches_model(self):
        for name in FIXTURE_NAMES:
            with self.subTest(fixture=name):
                input_dict = load_json(FIXTURES / name)
                GravityInput.model_validate(input_dict)

                result = gravity.run(input_dict)

                output = GravityOutput.model_validate(result)
                self.assertGreaterEqual(len(output.schemes), 1)
                self.assertIn("steel", [s.system for s in output.schemes])
                # Must survive a JSON round trip unchanged (what the CLI writes).
                self.assertEqual(json.loads(json.dumps(result)), result)

    def test_run_does_not_mutate_input(self):
        input_dict = load_json(FIXTURES / "simple_rect.json")
        before = copy.deepcopy(input_dict)
        gravity.run(input_dict)
        self.assertEqual(input_dict, before)

    def test_sample_output_matches_run_on_simple_rect(self):
        result = gravity.run(load_json(FIXTURES / "simple_rect.json"))
        self.assertEqual(result, load_json(SAMPLE_OUTPUT))

    def test_every_scheme_has_warnings_list(self):
        result = gravity.run(load_json(FIXTURES / "simple_rect.json"))
        for scheme in result["schemes"]:
            self.assertIsInstance(scheme["warnings"], list)


class TestSpecExamples(unittest.TestCase):
    """The literal JSON examples in the spec must validate against models.py."""

    def test_spec_2_1_input_example(self):
        # simple_rect.json is the section 2.1 example verbatim.
        GravityInput.model_validate(load_json(FIXTURES / "simple_rect.json"))

    def test_spec_3_1_output_example(self):
        # Section 3.1 example verbatim, except "alternates", which the spec shows as a
        # placeholder string ("...top 3 runner-up grids, same shape, slimmer...").
        example = {
            "schemes": [
                {
                    "system": "steel",
                    "rank_score": 0.62,
                    "grid": {"x_ft": [0, 30, 60, 90, 120, 150], "y_ft": [0, 30, 60, 90]},
                    "columns": [
                        {"id": "B-2", "x_ft": 30, "y_ft": 30, "stack": [
                            {"level": "L1", "size": "W12x65", "Pu_kips": 410, "Pd_kips": 180, "Pl_kips": 95}
                        ]}
                    ],
                    "bays": [
                        {"id": "A1-B2", "level": "L2", "span_x_ft": 30, "span_y_ft": 30,
                         "governing_occupancy": "office", "ll_psf": 50, "sdl_psf": 25,
                         "framing": {"slab": "3in 18ga comp deck + 3.25 LW", "beam": "W16x26 @ 10ft",
                                     "girder": "W21x44", "depth_in": 27.25, "self_wt_psf": 52, "db_row": 1184}}
                    ],
                    "levels": [
                        {"level": "L2", "seismic_weight_kips": 1420, "center_of_mass_ft": [75.0, 45.0],
                         "max_structure_depth_in": 27.25, "clear_height_ft": 9.4}
                    ],
                    "foundation_reactions": [{"column_id": "B-2", "D_kips": 180, "L_kips": 95, "Lr_kips": 8}],
                    "quantities": {"steel_tons": 142, "concrete_cy": 610, "timber_cf": 0},
                    "totals": {"cost_usd": 3150000, "cost_per_sf": 24.5, "carbon_kgco2e": 410000,
                               "carbon_per_sf": 31.9, "max_depth_in": 27.25},
                    "alternates": [],
                    "warnings": ["Zone z3 requires 60 ft clear span; DB max for steel is 45 ft — flagged as transfer girder"],
                }
            ]
        }
        GravityOutput.model_validate(example)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.good = load_json(FIXTURES / "simple_rect.json")

    def assert_rejected(self, bad: dict):
        with self.assertRaises(ValidationError):
            gravity.run(bad)

    def test_missing_required_field(self):
        bad = copy.deepcopy(self.good)
        del bad["outline"]
        self.assert_rejected(bad)

    def test_unknown_field_rejected(self):
        bad = copy.deepcopy(self.good)
        bad["constraints"]["min_bay"] = 20  # missing unit suffix
        self.assert_rejected(bad)

    def test_unknown_system(self):
        bad = copy.deepcopy(self.good)
        bad["systems"] = ["steel", "masonry"]
        self.assert_rejected(bad)

    def test_zone_on_undefined_level(self):
        bad = copy.deepcopy(self.good)
        bad["zones"][0]["level"] = "L9"
        self.assert_rejected(bad)

    def test_polygon_needs_three_points(self):
        bad = copy.deepcopy(self.good)
        bad["zones"][0]["polygon"] = [[0, 0], [120, 0]]
        self.assert_rejected(bad)

    def test_min_bay_exceeds_max_bay(self):
        bad = copy.deepcopy(self.good)
        bad["constraints"]["min_bay_ft"] = 50
        self.assert_rejected(bad)


class TestCli(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "gravity", *args],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )

    def test_cli_writes_valid_output_for_each_fixture(self):
        for name in FIXTURE_NAMES:
            with self.subTest(fixture=name), tempfile.TemporaryDirectory() as tmp:
                proc = self.run_cli("run", str(FIXTURES / name), "-o", tmp)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                GravityOutput.model_validate(load_json(Path(tmp) / "output.json"))

    def test_cli_rejects_invalid_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "bad.json"
            bad_path.write_text(json.dumps({"project_name": "x"}), encoding="utf-8")
            proc = self.run_cli("run", str(bad_path), "-o", tmp)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("does not match the input contract", proc.stderr)
            self.assertFalse((Path(tmp) / "output.json").exists())


if __name__ == "__main__":
    unittest.main()
