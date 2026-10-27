# Gravity Optimizer — rules for Claude Code

Spec: `GRAVITY_OPTIMIZER_SPEC.md` (section 1 is reproduced below).

## Project rules

- The spec is the source of truth. If the code needs to deviate from it, stop and ask.
- Steel is built first, end to end. Don't start concrete or mass timber until told to.
- The bay study SQLite DB is read-only and has no column sizes.
- Show your work on any engineering calculation: inputs, intermediate values, result.

## Guiding rules (spec section 1)

- Pure Python 3.11+, standard library + `sqlite3` + `pydantic` (v2) + `shapely` (polygon
  clipping / zone overlap). Nothing else unless asked.
- One entry point: `python -m gravity run <input.json> -o <output_dir>`, plus an importable
  `gravity.run(input_dict) -> dict` for the integration team.
- **Units everywhere: feet, kips, psf, ksf, inches only for member depths.** Every JSON field
  name carries its unit suffix (`span_ft`, `ll_psf`, `depth_in`).
- Coordinate system: origin at the lower-left of the building bounding box, +X right, +Y up,
  plan view. Must match what the lateral team and the front end use.
- Never interpolate the bay database unconservatively. Round spans **up** and loads **up** to
  the next row in the table.
- Every result carries a `warnings` list instead of throwing, so the front end always gets
  something to draw.
- The SQLite DB is read-only. Do not modify it. Wrap all SQL in one adapter module
  (`gravity/db_adapter.py`) so a schema change touches one file.

## Layout and commands

- `gravity/models.py` is the single source of truth for the input/output JSON contract.
- Run: `python -m gravity run tests/fixtures/simple_rect.json -o out/`
- Test: `python -m unittest discover -s tests -v` (from the repo root; pytest also works).
