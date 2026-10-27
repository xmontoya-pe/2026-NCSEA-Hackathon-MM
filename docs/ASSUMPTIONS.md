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

## Concrete (when built)

| # | Assumption | Decision |
|---|---|---|
| CC-1 | For flat plates, the column size in the DB row (`column_in`, used for the punching-shear check) is taken as the column size and reported in the concrete output. | H |
