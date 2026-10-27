# Gravity optimizer: integration examples

For the integration, front-end and lateral teams. The gravity optimizer is a black box:
**`input.json` in, `output.json` out.** The contract is defined in `gravity/models.py`;
the files here are generated from it or checked against it by tests.

| File | What it is |
|---|---|
| `sample_input.json` | A complete, valid input (spec section 2.1). |
| `sample_output.json` | The output for that input (spec section 3.1). |
| `input.schema.json`, `output.schema.json` | JSON Schema for both, generated from `gravity/models.py` with `python -m gravity schema -o examples`. |

> **Steel results are real; costs are not.** `run()` now runs the full steel pipeline
> (grids, bay study lookups, column sizing, scoring). Costs use the placeholder unit rates
> in `config/costs.json`. Concrete and mass timber return no scheme yet, with a warning.
> Regenerate `sample_output.json` after any engine change:
> `python -m gravity run sample_input.json -o out/` then copy `out/output.json` over it.

## How to call it

```bash
python -m gravity run sample_input.json -o out/   # writes out/output.json and out/warnings.log
```

```python
import gravity
output = gravity.run(input_dict)   # dict in, dict out
```

Invalid input is rejected: the CLI exits with status 2 and prints what is wrong;
`gravity.run` raises `pydantic.ValidationError`. Unknown keys are rejected too, so a
typo or a missing unit suffix fails loudly. Engineering problems never raise: they come
back as strings in each scheme's `warnings` list, so there is always something to draw.

## Conventions

- **Units:** feet, kips, psf; inches only for member depths (`depth_in`). Every numeric
  field name ends in its unit (`span_x_ft`, `ll_psf`, `Pu_kips`, `cost_usd`).
  `steel_tons` is US short tons (2,000 lb).
- **Coordinates:** plan view, feet, origin at the lower-left of the building bounding
  box, +X right, +Y up. Polygons are lists of `[x_ft, y_ft]`; the closing point is
  optional. *(Still to be confirmed with the vision and integration teams.)*
- **Levels:** listed bottom to top. The lowest level is treated as slab-on-grade and the
  highest as the roof.
- **Grid and column IDs:** grid lines along X are lettered from the left (A, B, C, ...),
  lines along Y numbered from the bottom (1, 2, 3, ...). Column `B-2` sits at the
  intersection of line B and line 2. Bay `A1-B2` is the bay from corner A1 to corner B2.
- **`columns[].stack[]`:** one entry per story. `level` is the level at the **bottom**
  of that story (the `L1` entry is the column from L1 to L2).
- **`bays[]` and `levels[]`:** one entry per framed level (above slab-on-grade, roof
  included). A bay's `level` is the floor it frames.
- **Column loads:** `Pd_kips`, `Pl_kips`, `Plr_kips` (roof live), `Psnow_kips` are
  service loads; `Pu_kips` is the factored load from `governing_combo`, one of
  `1.4D`, `1.2D+1.6L+0.5Lr`, `1.2D+1.6L+0.5S`, `1.2D+1.6Lr+1.0L`, `1.2D+1.6S+1.0L`.
- **`foundation_reactions`:** service loads by type (`D_kips`, `L_kips`, `Lr_kips`,
  `S_kips`) at the base of each column.
- **`rank_score`:** compares grids *within* one system (lower is better). Don't compare
  it across systems; use `totals` for that.

## What may still change

- `alternates`: up to three runner-up schemes with the same shape as a scheme (their own
  `alternates` are empty).
- `schemes`: steel only; concrete and mass timber come later, in the same shape.
- `bays`: bays with no framing in the bay study are left out and named in `warnings`.
- The coordinate convention above is pending confirmation.

Any contract change is made in `gravity/models.py`, regenerates the schema files, and
is announced to the team; tests fail if these examples drift from the models.
