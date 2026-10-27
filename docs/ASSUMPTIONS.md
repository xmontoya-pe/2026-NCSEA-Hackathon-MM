# Engineering assumptions

Every assumption the gravity optimizer makes that is not a direct code requirement is
listed here, with where it came from. The spec (`GRAVITY_OPTIMIZER_SPEC.md`, section 7)
records the decisions; this page is the reference for anyone reviewing results.

**Status:** the bay study database is marked "PRELIMINARY conceptual bay-study data;
verify by licensed engineer". Everything built on it is for scheme comparison only.

## Bay study database (`data/bay_studies.sqlite`)

| # | Assumption | Source |
|---|---|---|
| DB-1 | DB loads (`live_load_psf`, design SDL) are service (unfactored) loads. | Concrete validation: office 50+15 psf with SDL 15 → factored superimposed 122 psf = 1.2(15) + 1.6(65) |
| DB-2 | Each bay design accounts for its own framing self-weight internally; its load inputs are superimposed loads only. `columns.py` therefore adds framing self-weight to column dead load. | `assumptions`: "15.0 psf superimposed … + deck + member self-weight" |
| DB-3 | `steel_psf` is beams/joists + girders only (no columns, deck, bridging or connections). | Row 1: W12X22 @ 10 ft = 2.2 psf + W14X22 @ 20 ft = 1.1 psf = 3.3 psf; `assumptions` "Steel psf: Framing only" |
| DB-4 | DB carbon (`gwp_kgco2e_per_ft2`) is A1–A3 floor framing only and excludes columns, lateral system, foundations, studs and connections. Column carbon is added separately. | `db_info.gwp` |
| DB-5 | `span_1_ft` is the beam (infill) span and `span_2_ft` the girder span. | `db_info` |
| DB-6 | Steel bay designs use no live load reduction. Bay lookups therefore use unreduced LL (conservative); LL reduction (spec 4.1) is applied only to column loads. | `assumptions` STL_COMP "Design LL … No LL reduction" |

## Load lookup

| # | Assumption | Decision |
|---|---|---|
| L-1 | **SDL above the design SDL is added to LL for the lookup.** Each steel system is designed for one fixed SDL (15 psf). If the actual SDL is higher, the lookup uses `LL_lookup = LL + max(0, SDL_actual − SDL_design)`, and a warning names the bay and the excess. Conservative because LL carries the larger load factor (1.6 vs 1.2). | B |
| L-2 | **Partitions are not in the steel designs.** Steel `LL=50` rows exclude partitions, so the partition load from `config/occupancies.json` is added to LL for the lookup (office: 50 + 15 = 65 psf, which rounds up to the `LL=100` row). | C |
| L-3 | **Round up, never interpolate.** Spans round up to the next span in the table and loads to the next LL row. A bay whose load exceeds the table (e.g. floor LL above 100 psf, such as `storage_light` or `mechanical`) is reported as infeasible. | Spec section 1 |
| L-4 | **Vibration criterion by occupancy.** `config/occupancies.json` names the bay-study criterion per occupancy: `office` and `residential_private` use `Walking b=0.03`; all others use the `Strength` rows. | D |
| L-5 | **Roof load** for the lookup is `max(Lr, flat roof snow)`. Flat roof snow defaults to 0 psf if not given, with a warning. | G |
| L-6 | **Vibration rows below the lookup LL use an envelope.** If a vibration criterion row was designed for a lower LL than the lookup needs (office: walking rows are LL=50, lookup is 65 → 100), the Strength row at the rounded-up LL and the vibration row with the same spans and spacing are compared, and the one with more steel (then the deeper one; ties go to Strength) is used. A warning says no single DB design is checked for both. | 2 |
| L-7 | A vibration row satisfies strength at its own design LL. | DB `assumptions` STL_COMP: "vibration/rhythmic columns also satisfy 50 psf strength" |

## Zones and loads (`gravity/loads.py`)

| # | Assumption | Decision |
|---|---|---|
| Z-1 | Typical-zone copies carry the occupancy only, not `no_columns`: a no-column requirement applies only to the story it was drawn on. To keep a room column-free on several stories, draw the `no_columns` zone on each of those levels. | Confirmed (decisions log) |
| Z-2 | An explicit zone drawn on the roof level (e.g. `mechanical`) replaces the `roof` occupancy where it overlaps. | Extends decision 7 |
| Z-3 | An occupancy key not in `config/occupancies.json` falls back to `default_occupancy`, with a warning. | Spec 1 (warnings, not errors) |
| Z-4 | Roof Lr reduction (ASCE 7-22 4.8) assumes a flat roof (R2 = 1). Roofs with Lo above 20 psf are not ordinary roofs and are not reduced. | Simplification |
| Z-5 | Governing occupancy of a bay = largest LL + partitions of any overlapping zone (ties: larger area fraction). The bay's vibration criterion is the most stringent of all overlapping zones' criteria (`vibration_precedence` in config), so a bay half office, half corridor keeps the office walking check. | Spec 4.1, decision D |
| Z-6 | KLL per ASCE 7-22 Table 4.7-1 for exterior columns **without cantilever slabs**: interior, edge and corner columns 4 (with cantilever slabs edge would be 3 and corner 2); beams 2. No cantilever slabs are assumed. Set in `config/occupancies.json`. | Decisions log |
| Z-7 | Floor and roof SDL are 15 psf (`config/sdl.json`), equal to the bay study design SDL. Placeholder until project values are set. | Placeholder |
| Z-8 | Zone pieces smaller than 1 sq ft after clipping are slivers and are ignored. | Geometry tolerance |
| Z-9 | A bay is LL-reducible only if every occupancy it overlaps is reducible. | Conservative |

## Grids and bays (`gravity/grid.py`, `gravity/bays.py`)

| # | Assumption | Decision |
|---|---|---|
| G-1 | The outline's vertex coordinates are always grid lines, so re-entrant corners (L-shapes) get columns. Grids are generated between those lines. | Spec 4.2 interpretation |
| G-2 | Strategy A: each dimension span kept, split into 2 or 3 equal bays, or merged with its neighbor (lines skipped). Strategy B: equal bays near each bay study span size, or bays of that size plus one odd end bay of at least `min_bay_ft`. A segment shorter than `min_bay_ft` stays one bay. | Spec 4.2 |
| G-3 | **A column removed for a `no_columns` zone is removed from that story and every story above it** (no transfer girders): the framing at every level above spans over the zone. | Confirmed (decisions log) |
| G-4 | Cells around removed columns merge into the smallest bay (shortest longest span, then smallest area) whose four corners all have columns. Its spans are the full merged dimensions. | Spec 4.2 ("merged bay becomes a long-span bay") |
| G-5 | Columns are placed only where grid lines cross inside or on the outline; cells whose center is outside the outline are not framed. Columns within 2 ft of the outline edge (but not on it) get a warning. | Spec 4.2 |
| B-1 | Per bay, options within the allowable depth are preferred; among them the one with the lowest weighted score (`objective_weights` applied to cost, depth and carbon, min-max normalized across that bay's options) is used. If none fits the depth, the shallowest is used, with a warning. | Spec 4.3, 4.4, 4.6 |
| B-2 | Candidates rank by: fewest infeasible bays, then fewest bays over the allowable depth, then the weighted score (spec 4.6: cost, max depth and carbon, each min-max normalized across the system's candidates, times `objective_weights` as given), then fewest columns, then grid id. Cost and carbon are framing + columns. Infeasible and over-depth bays are therefore penalized ahead of any score difference, never dropped (spec 4.3, 4.4). `python -m gravity summary` still ranks by framing cost alone (no columns). | Spec 4.6, 4.7 |
| B-3 | Roof bays use no vibration criterion (Strength rows only). | DB has none for roofs |

## Weights, cost and carbon

| # | Assumption | Decision |
|---|---|---|
| W-1 | Framing self-weight = `steel_psf` + slab/deck weight. The slab/deck weight (69.0 psf for STL_COMP) is copied from the DB `assumptions` table into `config/systems.json` rather than parsed from text. | F |
| C-1 | **Cost is not in the DB.** It is computed from DB quantities × unit rates in `config/costs.json`. Those rates are placeholders (order-of-magnitude US national averages) and must be verified by an estimator. | A |
| C-2 | Roof deck rows with a blank gauge are priced at the 18 ga rate (conservative). | Data-quality note |
| C-3 | `steel_tons` in the output is US short tons (2,000 lb). Carbon factors in the DB are per metric tonne, so carbon = short tons × 0.9072 × factor. | Units |
| C-4 | Composite stud counts in `des_steel_composite` are per member: `beam_studs` per beam, `girder_studs` per girder. Studs per sq ft = beam_studs / (beam span × spacing) + girder_studs / (beam span × girder span). | Interpretation of the DB; check with the bay study author |
| C-5 | Joist roofs: joist steel = KCS `weight_plf` (`ref_kcs_joists`) / spacing, priced at the joist rate; the rest of `steel_psf` is girders at the W-shape rate. A joist missing from `ref_kcs_joists` prices all steel at the W-shape rate (conservative), with a warning. | Data split |
| W-2 | Roof deck weight comes from `ref_roof_deck_spans` by deck type and gauge. | DB table |

## Columns and seismic weight (`gravity/columns.py`)

| # | Assumption | Decision |
|---|---|---|
| COL-1 | **Tributary area = each bay's area / 4 to each of its four corner columns** (half-bay method on an orthogonal grid). Merged long-span bays deliver to their own four corners only. | Spec 4.5 |
| COL-2 | The bay's governing occupancy (Z-5) applies to its whole tributary area for column loads. | Conservative simplification |
| COL-3 | Column dead load per level = framing self-weight (DB `steel_psf` + slab/deck) + SDL + partitions + cladding + column self-weight. **Partitions are carried as dead load** (never reduced). Bays with no feasible framing use the slab/deck weight only, with a warning. | Spec 4.5, decisions C/F |
| COL-4 | Column kind from the framed cells around it: 4 = interior, 1 = corner, otherwise edge. A re-entrant corner of an L-shape (3 cells) is an edge column. KLL then comes from Z-6. | Interpretation |
| COL-5 | Floor LL reduction (ASCE 7-22 4.7.2) uses AT = sum of the tributary areas of the reducible floors the segment supports; the 0.5Lo limit applies for one floor, 0.4Lo for two or more. Floors with Lo > 100 psf or non-reducible occupancies are added unreduced. Roof Lr is reduced per 4.8 with the column's roof tributary area (flat roof, R2 = 1). | Spec 4.1, 4.5 |
| COL-6 | Load combinations: ASCE 7-22 2.3.1 gravity combos 1.4D; 1.2D+1.6L+0.5Lr; 1.2D+1.6L+0.5S; 1.2D+1.6Lr+1.0L; 1.2D+1.6S+1.0L. Pu is the largest; ties go to the first listed. | Spec 4.5 |
| COL-7 | Shapes: W12 and W14 from the AISC Shapes Database v16.0 CSV (`data/aisc_shapes.csv`, US-unit columns). Shapes slender for compression (Table B4.1a: bf/2tf > 0.56√(E/Fy) or h/tw > 1.49√(E/Fy)) are excluded, so E7 is never needed. Fy = 50 ksi (A992), E = 29,000 ksi. | Spec 4.5 |
| COL-8 | Capacity: AISC 360-22 E3 flexural buckling about the weak axis, K = 1.0, L = story height (braced at every floor in both directions). No torsional buckling check (doubly symmetric W shapes, not governing). | Spec 4.5 |
| COL-9 | Splice every 2 stories (`splice_every_n_levels`); each tier gets the lightest shape whose Pu/φPn ≤ 0.90 (`target_ratio`) in every story of the tier (ties: lower worst ratio). If none qualifies, the shape with the lowest ratio is used, with a warning. | Spec 4.5 |
| COL-10 | Column self-weight is iterated: size without it, add the chosen shapes' weight (plf × story height) to every segment below, resize; stop when the shapes stop changing (max 5 iterations, warning if not converged). | Spec 4.5 |
| COL-11 | Perimeter cladding load = `cladding_plf` (config/systems.json) × half the perimeter length to each neighboring perimeter column, per level. **Placeholder 0 plf** until the envelope is known; a warning says so. | Placeholder |
| COL-12 | Column tons = Σ weight_plf × story height / 2000 (short tons); cost = tons × `columns_usd_per_ton` (config/costs.json, placeholder); carbon = tons × 0.90718 × DB `carbon_factors` `hot_rolled_sections` (kg CO2e/t). | C-1, C-3 |
| COL-13 | Seismic weight per level (ASCE 7-22 12.7.2): framing + SDL + partitions over each bay, + 25% of floor LL in occupancies marked `seismic_storage` (config/occupancies.json), + 20% of flat roof snow when it exceeds 30 psf, + half the column weight of the story below and of the story above, + cladding. Center of mass = weight-weighted centroid of those items (bay centroids, column points). | Spec 3.2 |

## Output (`gravity/optimizer.py`)

| # | Assumption | Source |
|---|---|---|
| OUT-1 | `clear_height_ft` at a framed level = floor-to-floor height of the story below − (deepest framing at that level + `plenum_allowance_in`) / 12. | Spec 4.4 (same depth budget) |
| OUT-2 | Bays with no DB framing are left out of `bays` (the contract requires `framing`); each one keeps its own warning, and a summary warning gives the count. | Contract (`models.Bay`) |
| OUT-3 | `foundation_reactions` are the service loads of each column's bottom segment: D (with column self-weight), L **reduced** per COL-5, Lr reduced, S. | Spec 3.1 |
| OUT-4 | `quantities.steel_tons` = framing (DB `steel_psf` × bay area / 2000) + columns (COL-12). `concrete_cy` is the slab concrete on deck from the DB. Deck, studs and connections are in cost only. | C-1, C-3 |
| OUT-5 | A requested system that is not built yet (concrete, mass timber) returns no scheme; every returned scheme carries a warning naming it. | Spec 1 (warnings, not errors) |

## Front end integration (`bones_service/gravity_bridge.py`)

| # | Assumption | Source |
|---|---|---|
| INT-1 | The pad's levels are the elevated levels (cumulative `h`). Gravity gets `L1` at elevation 0 (slab on grade, story height = the first pad level's `h`), then `L2`, `L3`, … for the pad levels, the top one named `ROOF`. | Pad file format |
| INT-2 | Each load area with an occupancy chosen in the loads table becomes one zone per rectangle on its level. Areas with no occupancy are not sent, so gravity applies `default_occupancy` there (with its warning). Roof areas are not sent: the roof is `roof` occupancy. | User decision: occupancy dropdown |
| INT-3 | The LL and SDL typed in the loads table are not sent. LL, partitions, reducibility and vibration criterion come from the occupancy; SDL from `config/sdl.json`. | User decision: no contract change |
| INT-4 | Roof snow p_f from the hazard panel is not sent; gravity reads `settings.flat_roof_snow_psf` from `config/occupancies.json` (0 with a warning when null). | No contract change |
| INT-5 | `typical_zones_apply_to_all_levels` is false (the pad copies load areas explicitly). `dimension_lines` are the sketch's grid lines plus the outline extents, or the outline's vertex coordinates when no grid was read. No `no_columns` zones are sent (the pad has no way to draw one yet). | Pad file format |
| INT-6 | `constraints` (min/max bay, min ceiling height, plenum) come from the gravity card, with the spec 2.1 example values as defaults; `objective_weights` and `systems` use the spec 2.1 defaults (`steel` only). | Spec 2.1 |

## Concrete (when built)

| # | Assumption | Decision |
|---|---|---|
| CC-1 | For flat plates, the column size in the DB row (`column_in`, used for the punching-shear check) is taken as the column size and reported in the concrete output. | H |
