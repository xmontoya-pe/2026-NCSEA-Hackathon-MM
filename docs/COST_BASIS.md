# Cost basis: unit rate schedule and rationale

**Status: placeholder.** Every rate here is an order-of-magnitude US national average,
compiled from general industry knowledge without a cost database or current bids. Use
them to compare schemes, not to budget. An estimator should check them (or replace them
with RSMeans or local subcontractor pricing) before anyone relies on cost totals or on
the cost ranking between systems.

The rates live in `config/costs.json`, the single place to change them; no cost is
hardcoded in the code. This page explains what each rate covers, why that value was
picked, and how the optimizer uses it. The engineering assumptions behind the quantities
are in `docs/ASSUMPTIONS.md` (C-1 to C-8, COL-12, COLC-5, COLT-4).

The tool does not produce construction schedules (durations or sequencing); "schedule"
here means the unit rate schedule.

## Basis of every rate

| Item | Basis |
|---|---|
| What a rate includes | Installed cost: material, fabrication, labor, equipment, and the subcontractor's overhead and profit |
| What it excludes | General contractor markups, design contingency, escalation, sales tax, fireproofing, special connections, the lateral system and foundations |
| Currency and year | USD, 2025 |
| Location factor | 1.0 (national average) |
| Ton | US short ton, 2,000 lb. The bay study gives steel, rebar and PT in lb per sq ft of floor, so tons per sq ft = psf / 2000 |
| Range | Each rate has a low and a high next to the value used. They show how uncertain it is; the optimizer uses only the value |

**Why the bay study needs this:** it has no cost data. It does give quantities per sq ft
(steel psf, studs, concrete cy/sf, rebar psf, PT psf, glulam bf/sf, CLT thickness), so
cost = quantity x unit rate. Carbon is different: the bay study gives framing carbon per
sq ft directly, and its `carbon_factors` table is used for columns (see the end of this
page).

## Steel

| Rate | Value | Range | Applies to | Rationale |
|---|---|---|---|---|
| Wide-flange framing | $5,000 /ton | 4,000–6,500 | W beams and girders (`STL_COMP`, `STL_ROOF_W`, W girders of `STL_ROOF_J`) | Typical fabricated and erected price for light-to-medium gravity framing with simple shear tabs |
| Columns | $5,500 /ton | 4,500–7,000 | W12/W14 gravity columns from `columns.py` | Framing rate plus an allowance for base plates and splices, which cost more per ton than beam connections |
| Open-web joists | $3,800 /ton | 3,000–5,000 | KCS joists of `STL_ROOF_J` | Joists are shop-standard and cheaper per ton than W shapes; includes bridging |
| Shear studs | $4.00 each | 3–6 | `beam_studs` and `girder_studs` | 3/4 in. studs welded through deck, material and labor |
| Composite floor deck | $4.50 /sf | 3.50–6.00 | `STL_COMP` 2VLI deck | The bay study does not state the gauge; priced as typical 20 ga |
| Concrete on deck | $325 /cy | 250–425 | `STL_COMP` slab (`concrete_cy_per_sf`) | Small pours on deck (pump, place, finish, cure) cost more per cy than mass pours |
| Slab reinforcing on deck | $0.75 /sf | 0.50–1.25 | `STL_COMP` slab | The bay study reports no slab steel; allowance for welded wire or light bars |
| Roof deck, 1.5B 20 ga | $2.75 /sf | 2.25–3.50 | `STL_ROOF_W`, `STL_ROOF_J` | Typical installed price |
| Roof deck, 1.5B 18 ga | $3.25 /sf | 2.75–4.00 | same | Heavier gauge. Also used for rows whose gauge is blank (conservative) |
| Roof deck, 3N 20 ga | $4.25 /sf | 3.50–5.25 | same | Deeper deck for longer spans |
| Roof deck, 3N 18 ga | $5.00 /sf | 4.00–6.00 | same | |

**How a steel bay is priced (per sq ft):** beams and girders (steel psf / 2000 x $5,000),
plus studs per sq ft x $4, plus deck, plus concrete cy/sf x $325, plus $0.75 slab
reinforcing. Roof bays are steel plus roof deck. On joist roofs the joist weight
(KCS plf / spacing) is priced at the joist rate and the rest at the W-shape rate.
**Columns:** tons x $5,500.

## Concrete

| Rate | Value | Range | Applies to | Rationale |
|---|---|---|---|---|
| Structural concrete | $300 /cy | 240–400 | Slab, beam and joist concrete (`concrete_cy_per_sf`), and concrete columns | 5 ksi normal weight, pumped, placed, finished and cured. Large pours, so cheaper per cy than concrete on deck |
| Formwork, flat plate | $9.00 /sf of floor | 7–12 | `CONC_RC_FP`, `CONC_PT_FP` | Flat soffit with flying or table forms reused floor to floor; the simplest forming |
| Formwork, one-way slab and beams | $11.00 /sf of floor | 9–14 | `CONC_OW_PTB` | Adds beam sides and soffits |
| Formwork, pan joists | $12.00 /sf of floor | 10–15 | `CONC_PAN` | Rented pans plus joist and beam forms; the most forming labor |
| Reinforcing | $2,800 /ton | 2,200–3,600 | Mild steel (`rebar_psf`) and column bars | Grade 60, furnished, fabricated and placed |
| Post-tensioning | $4.00 /lb | 3.00–5.50 | `pt_psf` | Unbonded 0.5 in. monostrand, installed and stressed, anchors included |
| Stud rails | $1.00 /sf of floor | 0.50–2.00 | Flat plate rows marked for shear studs | **Allowance only:** the bay study gives no rail count |
| Column formwork | $18.00 /sf of contact area | 14–24 | Square columns from `columns.py` | Contact area = perimeter x height |

The three formwork rates are ordered on purpose (flat plate < one-way < pan joist). That
order is what makes flat plates competitive despite more concrete. Change one and the
concrete system the optimizer picks can change.

**How a concrete bay is priced (per sq ft):** concrete cy/sf x $300, plus the formwork
rate for the system, plus rebar psf / 2000 x $2,800, plus PT psf x $4, plus $1.00 where
the row has stud rails. **Columns:** concrete cy x $300, plus bars (1% of the area) in
tons x $2,800, plus contact area x $18.

**Worked example** (bay study row 7736, PT flat plate, 30 x 30 ft office, 8 in. slab):

| Item | Calculation | $/sf |
|---|---|---|
| Concrete | 0.024691 cy/sf x $300 | 7.41 |
| Formwork | flat plate | 9.00 |
| Rebar | 0.94 psf / 2000 x $2,800 | 1.32 |
| PT | 0.96 psf x $4.00 | 3.84 |
| Stud rails | allowance | 1.00 |
| **Total** | | **22.56** |

## Mass timber

| Rate | Value | Range | Applies to | Rationale |
|---|---|---|---|---|
| Glulam beams and girders | $7.00 /bf | 5–10 | `timber_bf_per_sf` | CNC-fabricated and erected, with concealed connections. About $84 /cf (about $3,000 /m³) |
| Glulam columns | $8.00 /bf | 6–11 | Square columns from `columns.py` | Framing rate plus an allowance for base and cap connections |
| CLT panels | $45 /cf | 35–60 | `clt_thickness_in` x floor area | Supplied and installed. 5-ply (6.875 in.) is about $26 /sf of floor |
| Concrete topping | $6.00 /sf of floor | 4.50–8.00 | `MT_FLOOR` 3 in. topping | Concrete, mesh, pump and finish. Acoustic mat excluded |

**How a timber bay is priced (per sq ft):** bf/sf x $7, plus CLT thickness / 12 x $45,
plus $6 topping on floors (none on the roof). **Columns:** cf x 12 bf/cf x $8.

**Worked example** (bay study row 6535, 30 x 30 ft office floor, glulam beams at 15 ft):

| Item | Calculation | $/sf |
|---|---|---|
| Glulam | 2.649 bf/sf x $7 | 18.54 |
| CLT | 6.875 in. / 12 x $45 | 25.78 |
| Topping | 3 in. | 6.00 |
| **Total** | | **50.32** |

## Results these rates give

The best scheme per system, framing plus columns, averaged over all framed area (floors
and roof), from `python -m gravity report`. These were run before the depth penalty was
scaled by inches over, which can change which timber grid ranks first; rerun the report
for current numbers.

| Fixture | Steel | Concrete | Mass timber |
|---|---|---|---|
| simple_rect | $20.04 /sf | $27.63 /sf (PT flat plate) | $46.63 /sf |
| l_shape | $19.47 /sf | $26.28 /sf (PT flat plate) | $44.00 /sf |
| no_column_zone | $20.73 /sf | $27.37 /sf (pan joists) | $35.34 /sf, with 6 bays the bay study can't frame |

## What an estimator should look at first

In order of how much they move the comparison:

1. **CLT ($45 /cf) and glulam ($7 /bf).** Together they are nearly 90% of a timber
   floor bay's cost. CLT may be low and glulam high; check both against current
   supplier quotes.
2. **Concrete formwork** ($9 / $11 / $12 per sf). The largest single concrete item; it
   decides between flat plate and the beam systems.
3. **Steel framing and column $/ton.** The biggest driver of steel cost.
4. **Allowances with no bay study quantity behind them:** stud rails, slab reinforcing on
   deck, and the connection allowances inside the column rates.
5. **Scope left out that falls unevenly:** fireproofing (steel needs it; exposed timber
   and concrete may not), acoustic mats under the timber topping, and the lateral
   system. Leaving out fireproofing makes steel look cheaper than it would in a full
   building cost.

## Carbon

Carbon is not priced and has no rates in this file. Framing carbon comes straight from
the bay study (`gwp_kgco2e_per_ft2`, A1–A3, framing only). Column carbon uses the bay
study's `carbon_factors` table: `hot_rolled_sections` (per metric tonne) for steel,
`concrete_5000psi` (365 kg/m³) and `rebar_fabricated` (854 kg/t) for concrete, and
`glulam` (137 kg/m³) for timber. See `docs/ASSUMPTIONS.md` DB-4, COL-12, COLC-5 and
COLT-4.

## Changing a rate

Edit the `value` in `config/costs.json` and rerun. Nothing else needs to change. Keep
`low` and `high` up to date so the uncertainty is visible, and note the source in the
`note` field (for example "ABC Steel quote, March 2026").
