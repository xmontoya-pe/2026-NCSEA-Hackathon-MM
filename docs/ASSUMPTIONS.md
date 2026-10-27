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
| L-8 | Concrete and mass timber load cases already include partitions ("Office 50+15 psf" is LL 65), so L-2 (LL + partitions) lands on them directly: office 65 → the 65 row, corridor 80 → the 100 row. Concrete roofs have only "Roof: 30 psf snow" rows, so Lr 20 rounds up to 30. | DB `criteria per system`, `assumptions` Loads |
| L-9 | Concrete DB systems keep floor and roof rows under one `system_id`; lookups filter on the `use` column (`shared_floor_roof` in config/systems.json). | DB `systems` (use 'Floor/Roof') |
| V-1 | Vibration criteria by DB system (`criteria` in config/systems.json): mass timber floors map `Walking b=0.03` / `b=0.02` to the DB's `Walking β=0.03 (office)` / `β=0.02 (office)`. Concrete studies have no vibration rows: concrete bays use the Strength rows and each bay warns that floor vibration was not checked. Timber and concrete fire rows (`Office + 1-hr fire`, ...) are not used. | DB `criteria per system`; concrete `Not checked: vibration` |

## Zones and loads (`gravity/loads.py`)

| # | Assumption | Decision |
|---|---|---|
| Z-1 | Typical-zone copies carry the occupancy only, not `no_columns`: a no-column requirement applies only to the story it was drawn on. To keep a room column-free on several stories, draw the `no_columns` zone on each of those levels. | Confirmed (decisions log) |
| Z-2 | An explicit zone drawn on the roof level (e.g. `mechanical`) replaces the `roof` occupancy where it overlaps. | Extends decision 7 |
| Z-3 | An occupancy key not in `config/occupancies.json` falls back to `default_occupancy`, with a warning. | Spec 1 (warnings, not errors) |
| Z-4 | Roof Lr reduction (ASCE 7-22 4.8) assumes a flat roof (R2 = 1). Roofs with Lo above 20 psf are not ordinary roofs and are not reduced. | Simplification |
| Z-5 | Governing occupancy of a bay = largest LL + partitions of any overlapping zone (ties: larger area fraction). The bay's vibration criterion is the most stringent of all overlapping zones' criteria (`vibration_precedence` in config), so a bay half office, half corridor keeps the office walking check. | Spec 4.1, decision D |
| Z-6 | KLL per ASCE 7-22 Table 4.7-1 for exterior columns **without cantilever slabs**: interior, edge and corner columns 4 (with cantilever slabs edge would be 3 and corner 2); beams 2. No cantilever slabs are assumed. Set in `config/occupancies.json`. | Decisions log |
| Z-7 | SDL per family (`config/sdl.json`) equals that family's bay study design SDL: steel 15/15, concrete 15/20, mass timber 10/15 psf (floor/roof), so no lookup is bumped by L-1. The timber study assumes 10 psf MEP/ceiling/finishes (exposed structure); the concrete study 20 psf on the roof. Placeholders until project values are set. | Placeholder |
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
| G-6 | **Equal bays are rounded up to the next 0.5 ft and the last bay of the span takes the remainder** (150 ft in 7 bays: 6 x 21.5 + 21, not 7 x 21.43). Applies to Strategy A subdivisions and Strategy B equal bays; outline vertices and dimension lines stay where they are, so a bay between them can still be an odd size. A rounded option whose bays fall outside `min_bay_ft`..`max_bay_ft` is dropped. | Confirmed (decisions log) |
| B-1 | Per bay, options within the allowable depth are preferred; among them the one with the lowest weighted score (`objective_weights` applied to cost, depth and carbon, min-max normalized across that bay's options) is used. If none fits the depth, the shallowest is used, with a warning. | Spec 4.3, 4.4, 4.6 |
| B-2 | `python -m gravity summary` ranks candidates by framing alone: infeasible bays, then bays over depth, then framing cost, then column count, then grid id. The scheme ranking (`run`, `report`) is S-1 to S-3. | Framing-only view |
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
| W-3 | Concrete self-weight = `concrete_equiv_thickness_in` / 12 × 150 pcf (slab, beams, joists; reinforcing included in the unit weight). | DB `assumptions` Materials: 5,000 psi NW (150 pcf) |
| W-4 | Mass timber self-weight = glulam (`timber_bf_per_sf` × 35 pcf / 12) + CLT 20.6 psf + 3 in. topping 37.5 psf on floors (`slab_deck_psf` 58.1 floor, 20.6 roof). | DB `assumptions` MT_FLOOR CLT, Glulam, Floor loads |
| W-5 | A bay with no feasible framing loads its columns with a fallback self-weight (`fallback_self_wt_psf`: RC flat plate 150, PT flat plate 125, one-way 125, pan joist 100, timber floor 66, timber roof 25 psf), with a warning. | Conservative placeholder |
| C-6 | Concrete cost/sf = `concrete_cy_per_sf` × concrete rate + formwork for the system + `rebar_psf` / 2000 × rebar rate + `pt_psf` × PT rate + a stud-rail allowance on flat plate rows with stud rails (all placeholders, config/costs.json). | A |
| C-7 | Mass timber cost/sf = `timber_bf_per_sf` × glulam rate + CLT volume (`clt_thickness_in` / 12) × CLT rate + topping on floors. | A |
| C-8 | `steel_tons` for concrete is reinforcing + PT strand (DB `rebar_psf` + `pt_psf`, plus column bars); for mass timber it is 0. `timber_cf` = glulam + CLT (+ glulam columns). | Units |

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

## Concrete and glulam columns (`gravity/columns.py`)

Loads, LL reduction, combinations and seismic weight are the same as COL-1 to COL-13.

| # | Assumption | Decision |
|---|---|---|
| COLC-1 | Square tied concrete column, φPn,max = 0.80 × 0.65 × [0.85 f'c (Ag − Ast) + fy Ast], f'c 5 ksi, fy 60 ksi, ρg 1%, 16 in. minimum, 2 in. steps (ACI 318-19 22.4.2). One size for the full height. Slenderness ignored; every concrete scheme warns. | Spec 4.5, decisions log |
| COLC-2 | Concrete target Pu/φPn,max ≤ 1.0: φPn,max already carries the 0.80 factor for accidental eccentricity (steel keeps 0.90). | Interpretation |
| COLC-3 | Flat plates: each column is at least the largest DB column (`24" sq col`, sized for punching) of the bays around it; if axial sizing needs more, the larger size is used and flagged. One-way and pan joist systems size columns by axial load only. | Decision H, spec 4.5 |
| COLC-4 | Column self-weight = b² × 150 pcf, iterated like COL-10. | Spec 4.5 |
| COLC-5 | Column quantities: concrete b² × height; bars ρg × b² × height × 490 pcf; forms 4b × height (sq ft contact). Carbon: `concrete_5000psi` (kg/m³) and `rebar_fabricated` (kg/t). | Units, DB `carbon_factors` |
| COLT-1 | Square glulam column, NDS LRFD: Fc' = Fc × KF 2.40 × φ 0.90 × λ × Cp; Emin' = Emin × 1.76 × 0.85; FcE = 0.822 Emin' / (le/d)²; Cp per NDS 3.7.1 with c = 0.9; le = story height, le/d ≤ 50. Placeholder Fc 2,300 psi, Emin 850,000 psi; widths 6¾, 8¾, 10¾, 12¼ in. | Spec 4.5, decisions log |
| COLT-2 | Time effect λ = 0.6 for 1.4D and 0.8 for every combination with live, roof live or snow load (NDS Table N3, occupancy live load); each story checks both. | Simplification (λ 0.8 is conservative for Lr and S) |
| COLT-3 | Glulam target ratio ≤ 0.90 (as steel: room for moment). One size for the full height. If 12¼ in. is not enough, it is used anyway with a warning. Every timber scheme warns that columns are preliminary. | Spec 4.5 |
| COLT-4 | Column quantities: b² × height (cf); priced per board foot (cf × 12) at the glulam column rate; carbon `glulam` (kg/m³). | Units, DB `carbon_factors` |

## Scoring and output (`gravity/scoring.py`, `gravity/optimizer.py`)

| # | Assumption | Decision |
|---|---|---|
| S-1 | Score = Σ weight × min-max normalized metric / Σ weights, over every candidate of one system. Metrics: total cost (framing + columns), max structure depth, total carbon (framing + columns). A metric equal for all candidates contributes 0. Lower is better. | Spec 4.6 |
| S-2 | `rank_score` = score + 1.0 per bay over the allowable depth + 100.0 per infeasible bay (`config/systems.json` `scoring`). Any penalty exceeds the largest score difference (1.0), so a clean candidate always ranks first and penalized candidates still appear. | Spec 4.3, 4.4 (penalize, not remove); confirmed (decisions log) |
| S-3 | Ties: fewest columns, then grid id, then structural system. The best candidate is the scheme; the next 3 are its `alternates` (their own `alternates` are empty). | Spec 4.7 |
| S-4 | A family with several structural systems (concrete: RC flat plate, PT flat plate, one-way slab on PT beams, pan joist) runs each system uniformly over the whole building and scores all their candidates together; alternates may be other concrete systems. | Decisions log |
| O-1 | Bays with no feasible framing are left out of a scheme's `bays` list (the contract requires `framing`); each still has a warning. | Confirmed (decisions log) |
| O-2 | `clear_height_ft` = height of the story below the level − (max framing depth at the level + `plenum_allowance_in`) / 12, the ceiling height left under that framing. | Spec 4.4 |
| O-3 | `foundation_reactions` are the service loads of the bottom column segment: D includes column self-weight, L is reduced (4.7 column reduction), Lr reduced (4.8). | Spec 3.1 |
| O-4 | `steel_tons` = framing (DB `steel_psf` × bay area) + columns; excludes deck, studs and connections. `concrete_cy` = slab concrete on deck (DB `concrete_cy_per_sf`). `cost_per_sf` and `carbon_per_sf` divide by the framed area of all framed levels, roof included. | Units and scope |
| O-5 | Systems not built yet (no `systems` and `columns` entry in `config/systems.json`) are skipped; every returned scheme carries a warning naming them. Every scheme also warns that costs are placeholders. | Spec 1 (warnings, not errors) |
| O-6 | Member sizes use the bay study and AISC labels as given (`W12X53`, `W14X22 @ 10ft`, `8" slab`, `24" sq col`, `6-3/4 x 36 @ 15ft`). For flat plates the bay's `girder` is the DB column (`24" sq col`); for one-way slabs and pan joists `beam`/`girder` are the DB's two members. Concrete columns are named `18x18 conc`, glulam columns `10.75x10.75 GL`. | Traceability |
| O-7 | A concrete scheme's first warning names its structural system and DB system, e.g. `Concrete system: PT flat plate (CONC_PT_FP)`; the output contract has no field for it. | Contract unchanged |

## Concrete

| # | Assumption | Decision |
|---|---|---|
| CC-1 | For flat plates, the column size in the DB row (`column_in`, used for the punching-shear check) is taken as the column size and reported in the concrete output (COLC-3). | H |
| CC-2 | One-way slab on PT beams: `span_1` is the PT beam span (24–60 ft) and `span_2` the slab span = column spacing (10–20 ft), so only bays with one side ≤ 20 ft are feasible. Both "Min depth" and "Min concrete" designs are options. | DB `db_info`, `assumptions` Layout |
| CC-3 | Flat plate rows with and without stud rails, and every pan module, are all options for a bay; the per-bay choice (B-1) picks among them. | Interpretation |
