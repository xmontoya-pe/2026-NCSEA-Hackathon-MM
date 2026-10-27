# Gravity Optimizer — Spec & Build Plan

Python module for the structural scheme comparison hackathon. Takes a dimensioned floor plan (already recognized by the vision team) plus drawn occupancy zones, pulls framing solutions from our SQLite bay study database, and returns three optimized gravity schemes: **steel**, **concrete**, **mass timber**.

Other teammates own: input screens, floor plan recognizer, lateral analysis, and the front end ↔ Python integration. This module's job is to be a clean, predictable black box: **JSON in → JSON out**.

---

## 1. Guiding rules (put these in CLAUDE.md)

- Pure Python 3.11+, standard library + `sqlite3` + `pydantic` (v2) + `shapely` (polygon clipping / zone overlap). Nothing else unless asked.
- One entry point: `python -m gravity run <input.json> -o <output_dir>`, plus an importable `gravity.run(input_dict) -> dict` for the integration team.
- **Units everywhere: feet, kips, psf, ksf, inches only for member depths.** Every JSON field name carries its unit suffix (`span_ft`, `ll_psf`, `depth_in`).
- Coordinate system: origin at the lower-left of the building bounding box, +X right, +Y up, plan view. Must match what the lateral team and the front end use — confirm before hour 1.
- Never interpolate the bay database unconservatively. Round spans **up** and loads **up** to the next row in the table.
- Every result carries a `warnings` list instead of throwing, so the front end always gets something to draw.
- The SQLite DB is read-only. Do not modify it. Wrap all SQL in one adapter module so a schema change touches one file.

---

## 2. Inputs

### 2.1 `input.json` (contract with the integration team)

```json
{
  "project_name": "Hackathon Test 1",
  "outline": [[0,0],[150,0],[150,90],[0,90]],
  "dimension_lines": {
    "x_ft": [0, 30, 60, 90, 120, 150],
    "y_ft": [0, 30, 60, 90]
  },
  "levels": [
    {"name": "L1",   "elev_ft": 0.0,  "floor_to_floor_ft": 15.0},
    {"name": "L2",   "elev_ft": 15.0, "floor_to_floor_ft": 13.0},
    {"name": "L3",   "elev_ft": 28.0, "floor_to_floor_ft": 13.0},
    {"name": "ROOF", "elev_ft": 41.0, "floor_to_floor_ft": null}
  ],
  "zones": [
    {"id": "z1", "level": "L2", "occupancy": "office",   "polygon": [[0,0],[120,0],[120,90],[0,90]]},
    {"id": "z2", "level": "L2", "occupancy": "corridor_above_first", "polygon": [[120,0],[150,0],[150,90],[120,90]]},
    {"id": "z3", "level": "L1", "occupancy": "assembly_movable_seats", "polygon": [[30,30],[90,30],[90,60],[30,60]], "no_columns": true}
  ],
  "typical_zones_apply_to_all_levels": true,
  "constraints": {
    "min_ceiling_height_ft": 9.0,
    "plenum_allowance_in": 18,
    "max_structure_depth_in": null,
    "min_bay_ft": 20,
    "max_bay_ft": 45
  },
  "objective_weights": {"cost": 0.5, "depth": 0.3, "carbon": 0.2},
  "systems": ["steel", "concrete", "mass_timber"]
}
```

Notes:
- `dimension_lines` are what the recognizer read off the sketch. The grid generator should **prefer** these lines but is allowed to subdivide or skip them.
- Zones may be drawn on one level and copied up (`typical_zones_apply_to_all_levels`). The copy goes to the framed floors only: not to the lowest level (treated as slab-on-grade) and not to the roof (always `roof` occupancy). An explicit zone on a level overrides the copy only where it overlaps it.
- Any part of a floor plate not covered by a zone gets `default_occupancy` (config, default `office`) and a warning.
- `no_columns: true` zones force a long-span solution over that area (or flag a transfer if spans exceed the DB). A `no_columns` zone on level Ln applies to the story from Ln up to the next level, so the framing at the next level spans over it. Only columns strictly inside the zone are removed; columns on its boundary stay.

### 2.2 `config/occupancies.json` (editable by engineers, not hardcoded)

Starter values from ASCE 7-22 Table 4.3-1 — **verify before trusting**:

| key | LL psf | partitions psf | LL reducible | notes |
|---|---|---|---|---|
| office | 50 | 15 | yes | |
| lobby_first_floor | 100 | 0 | yes | |
| corridor_first_floor | 100 | 0 | yes | |
| corridor_above_first | 80 | 0 | yes | |
| residential_private | 40 | 15 | yes | |
| residential_public_corridor | 100 | 0 | yes | |
| assembly_movable_seats | 100 | 0 | no | |
| retail_first_floor | 100 | 0 | yes | |
| storage_light | 125 | 0 | no* | *ASCE allows 20% for columns, multi-level; skip for hackathon |
| mechanical | 125 | 0 | no | firm standard, not an ASCE table value |
| roof | 20 | 0 | per 4.8 | use Lr, snow handled as a flat input for now |

Plus a separate SDL block per system type (ceiling/MEP/floor finish, e.g. 10–15 psf) in `config/sdl.json`.

### 2.3 SQLite bay study database

**Step 1 for Claude Code is to inspect it, not assume it.** Run `.tables` and `.schema`, pull a few rows from each table, and write `gravity/db_adapter.py` around what's actually there. The adapter must expose:

```python
def framing_options(system: str, span_x_ft: float, span_y_ft: float,
                    ll_psf: float, sdl_psf: float) -> list[BayFraming]
def available_spans(system: str) -> tuple[list[float], list[float]]
def available_loads(system: str) -> tuple[list[float], list[float]]   # LL and SDL values present in the table
```

`BayFraming` should carry (whatever the DB supports): deck/slab description, infill beam size & spacing, girder size, total depth (in), self-weight (psf), cost ($/sf), embodied carbon (kgCO2e/sf), and source row id for traceability.

**The DB has no column sizes.** Columns are always sized by `columns.py` (section 4.5). Column cost and carbon are computed from column tonnage/volume using unit rates in `config/systems.json` and added to the bay totals. Do not assume the DB's $/sf or carbon/sf include columns unless step 2 of section 6 proves otherwise.

---

## 3. Outputs

### 3.1 `output.json` — one object per system

```json
{
  "schemes": [
    {
      "system": "steel",
      "rank_score": 0.62,
      "grid": {"x_ft": [0,30,60,90,120,150], "y_ft": [0,30,60,90]},
      "columns": [
        {"id": "B-2", "x_ft": 30, "y_ft": 30, "stack": [
          {"level": "L1", "size": "W12x65", "Pu_kips": 410, "Pd_kips": 180, "Pl_kips": 95,
           "Plr_kips": 8, "Psnow_kips": 0, "governing_combo": "1.2D+1.6L+0.5Lr"}
        ]}
      ],
      "bays": [
        {"id": "A1-B2", "level": "L2", "span_x_ft": 30, "span_y_ft": 30,
         "governing_occupancy": "office", "ll_psf": 50, "sdl_psf": 25,
         "framing": {"slab": "3in 18ga comp deck + 3.25 LW", "beam": "W16x26 @ 10ft", "girder": "W21x44",
                     "depth_in": 27.25, "self_wt_psf": 52, "db_row": 1184}}
      ],
      "levels": [
        {"level": "L2", "seismic_weight_kips": 1420, "center_of_mass_ft": [75.0, 45.0],
         "max_structure_depth_in": 27.25, "clear_height_ft": 9.4}
      ],
      "foundation_reactions": [{"column_id": "B-2", "D_kips": 180, "L_kips": 95, "Lr_kips": 8, "S_kips": 0}],
      "quantities": {"steel_tons": 142, "concrete_cy": 610, "timber_cf": 0},
      "totals": {"cost_usd": 3150000, "cost_per_sf": 24.5, "carbon_kgco2e": 410000,
                 "carbon_per_sf": 31.9, "max_depth_in": 27.25},
      "alternates": [ "...top 3 runner-up grids, same shape, slimmer..." ],
      "warnings": ["Zone z3 requires 60 ft clear span; DB max for steel is 45 ft — flagged as transfer girder"]
    }
  ]
}
```

`governing_combo` is one of `1.4D`, `1.2D+1.6L+0.5Lr`, `1.2D+1.6L+0.5S`, `1.2D+1.6Lr+1.0L`, `1.2D+1.6S+1.0L` (section 4.5).

### 3.2 What the lateral team needs from us (agree on this early)

- Grid lines and column locations per level
- Seismic weight and center of mass per level (D + 25% storage LL + partitions per ASCE 12.7.2)
- Floor diaphragm type per system (rigid for concrete and steel comp deck; CLT flag as semi-rigid / needs review)
- Gravity column axial loads, so they can do leaning-column/P-delta if they want

Ask them what format they want it in. If they want something different, add an exporter — don't change the core model.

---

## 4. Module layout

```
gravity/
  __init__.py          # exposes run()
  __main__.py          # CLI
  models.py            # pydantic models for input & output (single source of truth for the contract)
  db_adapter.py        # ALL SQL lives here
  loads.py             # occupancy -> LL/SDL, zone-to-bay mapping, LL reduction
  grid.py              # candidate grid generation + clipping to outline
  bays.py              # bay framing lookup per grid candidate
  columns.py           # tributary areas, load accumulation, column sizing
  scoring.py           # cost/depth/carbon normalization and weighted score
  optimizer.py         # search loop per system
  exporters.py         # output.json, plus optional lateral-team format
config/
  occupancies.json
  sdl.json
  systems.json         # per-system span limits, DB name mapping, column unit costs/carbon,
                       # target_ratio, steel_splice_every_n_levels, material properties
data/
  aisc_shapes.csv      # W12/W14 properties (AISC export if available, else embedded subset)
tests/
  fixtures/simple_rect.json
  fixtures/l_shape.json
  fixtures/no_column_zone.json
  test_*.py
```

### 4.1 `loads.py`
- Map each bay polygon to the zones it overlaps (shapely intersection).
- Governing load for a bay = **max** LL of any overlapping zone (conservative, simple). Keep the area fractions in the output for later refinement.
- Live load reduction per ASCE 7-22 §4.7: `L = Lo * (0.25 + 15/sqrt(KLL*AT))`, KLL = 4 interior columns, 2 interior beams / edge columns (see table 4.7-1), limited to 0.5Lo for members supporting one floor and 0.4Lo for multiple floors. Not applied where LL > 100 psf or assembly occupancy. Roof uses §4.8.
- Partition load added only where the occupancy table says so.

### 4.2 `grid.py`
- Generate candidates per system from `available_spans(system)` within `min_bay_ft`..`max_bay_ft`.
- Strategy A: snap to recognized `dimension_lines`, optionally subdividing a dimension span into 2 or 3 equal bays.
- Strategy B: uniform grids that fill the bounding box (allow one odd end bay).
- Clip to the outline; drop columns outside, flag columns within 2 ft of the outline edge for a perimeter line.
- Remove columns strictly inside `no_columns` zones (columns on the zone boundary stay); the merged bay becomes a long-span bay.
- Keep the candidate count small (dozens, not thousands). Brute force is fine.

### 4.3 `bays.py`
- For each bay in a candidate: look up framing with spans/loads rounded up.
- Try both orientations (beams spanning X vs Y) and keep the better.
- If no DB row fits: mark the bay `infeasible`, attach a warning, and penalize the whole candidate rather than crash.

### 4.4 Depth constraint
`allowable_depth_in = (floor_to_floor_ft - min_ceiling_height_ft)*12 - plenum_allowance_in`, overridden by `max_structure_depth_in` if given. `floor_to_floor_ft` is that of the story **below** the framed level, whose ceiling the framing sits in (framing at L2 uses L1's height; roof framing uses the top story's). Candidates exceeding it at any bay get a heavy penalty, not removal, so we can still show "best you can do."

### 4.5 `columns.py` (simplified sizing — the DB has no columns)

**Loads**
- Tributary area by half-bay method (Voronoi is overkill for orthogonal grids). Edge and corner columns get the partial area automatically.
- Accumulate D, L (reduced per 4.1 using cumulative AT and KLL from ASCE 7-22 Table 4.7-1), Lr, and S (flat roof snow; see decisions log) from the roof down.
- D = framing self-weight from the DB row's `self_wt_psf` (only if the DB table doesn't already include it in its load basis — confirm in section 6 step 2) + SDL + partitions where applicable (partitions are treated as live for reduction purposes only if the team agrees; default: carry as dead, conservative) + column self-weight from the previous iteration + a perimeter cladding line load from `config/systems.json` (default 0 for the hackathon, flagged).
- Factored axial = max of the ASCE 7-22 §2.3.1 gravity combos:
  - `1.4D`
  - `1.2D + 1.6L + 0.5Lr`
  - `1.2D + 1.6L + 0.5S`
  - `1.2D + 1.6Lr + 1.0L`
  - `1.2D + 1.6S + 1.0L`
- Report Pd, Pl, Plr, Psnow, Pu, and governing combo per column per level (output fields `Pd_kips`, `Pl_kips`, `Plr_kips`, `Psnow_kips`, `Pu_kips`, `governing_combo`), and S in the foundation reactions (`S_kips`).

**Steel (build first)**
- Shapes: W12 and W14 column shapes only, Fy = 50 ksi, E = 29,000 ksi.
- Section properties (`A`, `rx`, `ry`, `weight`, flange/web slenderness) come from `data/aisc_shapes.csv`. If Renee provides an export of the AISC Shapes Database, load it from there. If not, embed a small table of the common W12/W14 column shapes and mark it `"source": "embedded — verify against AISC"` in the output.
- Compute capacity, don't hardcode it. AISC 360 Chapter E3, flexural buckling about the weak axis:
  - `KL/r = K·L_ft·12 / ry`, K = 1.0, L = floor-to-floor height of that story
  - `Fe = π²E / (KL/r)²`
  - if `KL/r ≤ 4.71·√(E/Fy)`: `Fcr = 0.658^(Fy/Fe) · Fy`, else `Fcr = 0.877·Fe`
  - `φPn = 0.90 · Fcr · A`
  - Exclude shapes with slender elements for compression (Table B4.1a) rather than implementing E7.
- Selection: lightest shape with `Pu / φPn ≤ target_ratio` (config, default 0.90 to leave room for the moment the lateral team won't send us).
- Stack: size the bottom of each 2-story tier and run that size through the tier (splice every 2 levels). For a 3-story building, that's one splice above L2 or none — let the config decide (`steel_splice_every_n_levels`, default 2).
- Column tonnage = Σ (weight_plf × length) → `steel_tons`, priced with the column $/ton and carbon/ton in `config/systems.json`.
- Hand-check test: one interior column on `simple_rect.json` must match a manual calc within 1% (Claude Code shows the numbers).

**Concrete and mass timber (after steel works)**
- Concrete: square tied column, `φPn,max = 0.80·0.65·[0.85f'c(Ag − Ast) + fy·Ast]`, 1% steel, f'c = 5 ksi, fy = 60 ksi, 16 in minimum, round up in 2 in increments. Ignore slenderness for the hackathon and flag it.
- Mass timber: square glulam, NDS column stability factor Cp with placeholder Fc / E values in `config/systems.json`, round up to standard glulam widths. Flag as preliminary.
- Constant size up the height for both in a 3-story building unless a column exceeds its ratio.

### 4.6 `scoring.py`
- Normalize cost, max depth, carbon across all candidates **within a system** (min-max to 0–1), weighted sum with `objective_weights`. Lower is better.
- Comparing **across** systems is the dashboard's job — we just report raw totals.

### 4.7 `optimizer.py`
For each system: generate grids → evaluate bays → size columns → score → return best + top 3 alternates. Deterministic (sort ties by fewest columns, then by grid id) so reruns give the same answer during the demo.

---

## 5. Hackathon timeline (6 hours)

| Time | Deliverable | Why first |
|---|---|---|
| 0:00–0:30 | Agree on `input.json` / `output.json` with teammates; commit `models.py` + a **stub `run()` returning canned output** | Unblocks the integration and lateral teams immediately |
| 0:30–1:15 | Inspect SQLite, write `db_adapter.py` + tests against real rows | Everything downstream depends on it |
| 1:15–2:00 | `loads.py` + occupancy config + zone mapping | |
| 2:00–3:00 | `grid.py` + `bays.py` for **steel only**, end to end | Get one system fully working before widening |
| 3:00–3:45 | `columns.py`, seismic weight per level | Lateral team needs this |
| 3:45–4:30 | Add concrete + mass timber (mostly config + DB mapping if the adapter is right) | |
| 4:30–5:15 | Integration with front end, fix contract mismatches | Always takes longer than planned |
| 5:15–6:00 | Demo fixtures, warnings polish, freeze | |

Cut list if behind (in order): alternates, LL reduction (just use full LL, flag it), timber column fallback, strategy A grid snapping.

---

## 6. Prompts to give Claude Code, in order

1. "Read GRAVITY_OPTIMIZER_SPEC.md. Create the package skeleton from section 4, `models.py` matching sections 2.1 and 3.1, and a `run()` that validates input and returns a hardcoded output for `tests/fixtures/simple_rect.json`. Add a CLI. Don't implement any engineering yet."
2. "Open `<path>/bay_study.sqlite`. Show me `.schema` and 5 sample rows per table. Propose how each column maps to `BayFraming` and `ColumnSize` before writing code. Flag anything ambiguous (units, load basis, whether self-weight is included in the table's load)."
3. "Implement `db_adapter.py` per section 2.3 with conservative round-up lookups. Write tests that hit real rows."
4. "Implement `loads.py` per 4.1 with tests, including a bay that straddles two zones and a no-column zone."
5. "Implement `grid.py` and `bays.py` per 4.2–4.4 for steel only. Run end to end on `simple_rect.json` and print a summary table."
6. "Implement the steel part of `columns.py` per 4.5 (loads, combos, AISC E3 sizing, 2-level tiers) and seismic weight per 3.2. Hand-check one interior column on `simple_rect.json` against a manual calc and show me every number: tributary area, reduced LL, each combo, KL/r, Fe, Fcr, φPn, ratio."
7. "Run steel end to end on all three fixtures. Print a summary table per fixture: grid, max depth, steel tons (beams/girders vs columns), cost, carbon, warnings."
8. *(after steel is demo-ready)* "Extend to concrete and mass timber per 4.5 and `config/systems.json`. Run all three fixtures and show a comparison table across systems."

Review each step's output before moving on — especially step 2 (DB units and load basis) and step 6 (the hand check).

---

## 7. Decisions log

| Decision | Status |
|---|---|
| Inputs and outputs are JSON, contract defined by `models.py` | **Settled** |
| Steel is built first and end to end before concrete/timber | **Settled** |
| DB has no column sizes; columns use simplified sizing (4.5) | **Settled** |
| Column cost/carbon computed separately from DB bay $/sf | **Settled** (verify DB doesn't already include columns) |
| Coordinate origin and units from the recognizer | Open — confirm with vision/integration team |
| Lateral picks its own frame lines vs. we propose them | Open — default: lateral picks |
| Roof snow load source | Open — default: flat input in `config`, 0 if not given, flagged |
| DB loads are service vs factored; self-weight included or not | Open — answered in section 6 step 2 |
| Column stack entries carry `Plr_kips`, `Psnow_kips` and `governing_combo`; foundation reactions carry `S_kips`; snow combos `1.2D+1.6L+0.5S` and `1.2D+1.6S+1.0L` added to 4.5 | **Settled** |
| `no_columns` zones remove only columns strictly inside the zone; columns on its boundary stay. A zone on level Ln applies to the story from Ln up to the next level | **Settled** |
| Depth limit at a framed level uses the floor-to-floor height of the story below it (4.4) | **Settled** |
| Typical zones copy to framed floors only (lowest level is slab-on-grade, roof is always `roof` occupancy); an explicit zone overrides copied zones only where it overlaps them | **Settled** |
