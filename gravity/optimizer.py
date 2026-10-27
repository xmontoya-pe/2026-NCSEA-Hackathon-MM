"""Per-system search loop.

Spec section 4.7. For each requested system: generate grids (``grid``), evaluate bays
(``bays``), size columns (``columns``), score (``scoring``), and return the best scheme
plus the top 3 alternates. Deterministic: ties sort by fewest columns, then grid id.

Ranking (docs/ASSUMPTIONS.md B-2): candidates with fewer infeasible bays always rank
first, then fewer bays over the allowable depth (spec 4.3 / 4.4: penalized, never
dropped), then the weighted score of ``scoring.weighted_scores``, then fewest columns,
then grid id. Cost and carbon include the columns.

Output shaping (docs/ASSUMPTIONS.md OUT-*): bays with no DB framing are left out of
``bays`` (the contract requires framing) and named in the warnings.
"""

from __future__ import annotations

from dataclasses import dataclass

from shapely.geometry import Polygon

from gravity.bays import CandidateResult, evaluate
from gravity.columns import ColumnDesign, design_columns, seismic_weights, story_heights
from gravity.grid import generate_grids, layout
from gravity.loads import ZoneMap, resolve_zones
from gravity.models import GravityInput
from gravity.scoring import Metrics, weighted_scores

# Systems with a DB mapping and column sizing. Concrete and mass timber join when built.
BUILT_SYSTEMS = ("steel",)
N_ALTERNATES = 3


@dataclass
class Evaluated:
    candidate: CandidateResult
    design: ColumnDesign
    metrics: Metrics
    score: float = 0.0

    def rank_key(self) -> tuple:
        c = self.candidate
        return (c.n_infeasible, c.n_over_depth, round(self.score, 9), c.layout.column_count,
                c.grid_id)


def optimize(inp: GravityInput, system: str) -> dict:
    """The best scheme for one system, as a ``models.Scheme`` dict, with its alternates."""
    zone_map = resolve_zones(inp)
    grids, grid_warnings = generate_grids(inp, system=system)
    input_warnings = zone_map.warnings + grid_warnings

    cache: dict = {}
    evaluated = []
    for g in grids:
        candidate = evaluate(inp, zone_map, layout(inp, zone_map, g), system, cache)
        design = design_columns(inp, zone_map, candidate, system)
        evaluated.append(Evaluated(candidate, design, Metrics(
            cost_usd=candidate.framing_cost_usd + design.cost_usd,
            max_depth_in=candidate.max_depth_in,
            carbon_kgco2e=candidate.framing_carbon_kgco2e + design.carbon_kgco2e,
        )))
    if not evaluated:
        return _empty_scheme(inp, system, input_warnings + ["No candidate grid; no scheme built"])

    for ev, score in zip(evaluated, weighted_scores([e.metrics for e in evaluated],
                                                    inp.objective_weights)):
        ev.score = score
    ranked = sorted(evaluated, key=Evaluated.rank_key)

    best = _scheme(inp, zone_map, ranked[0], system, input_warnings)
    best["alternates"] = [_scheme(inp, zone_map, ev, system, input_warnings)
                          for ev in ranked[1:1 + N_ALTERNATES]]
    return best


# ---------------------------------------------------------------------------
# Scheme dicts (models.Scheme)
# ---------------------------------------------------------------------------


def _scheme(inp: GravityInput, zone_map: ZoneMap, ev: Evaluated, system: str,
            input_warnings: list[str]) -> dict:
    cand, design = ev.candidate, ev.design
    seismic = {s.level: s for s in seismic_weights(inp, zone_map, cand, design, system)}
    heights = story_heights(inp)
    levels = list(zone_map.levels)

    bays, level_rows = [], []
    for level in cand.layout.bays_by_level:
        level_bays = [b for b in cand.bays if b.level == level]
        for b in level_bays:
            if b.framing is None:
                continue
            f = b.framing
            bays.append({
                "id": b.rect.bay_id, "level": level,
                "span_x_ft": _r(b.rect.span_x_ft), "span_y_ft": _r(b.rect.span_y_ft),
                "governing_occupancy": b.loads.governing_occupancy,
                "ll_psf": _r(b.loads.ll_psf), "sdl_psf": _r(b.loads.sdl_psf),
                "framing": {"slab": f.slab, "beam": f.beam, "girder": f.girder,
                            "depth_in": _r(f.depth_in), "self_wt_psf": _r(f.self_wt_psf),
                            "db_row": f.db_row},
            })
        depth = max((b.framing.depth_in for b in level_bays if b.framing), default=0.0)
        story_ft = heights[levels[levels.index(level) - 1]]
        s = seismic[level]
        level_rows.append({
            "level": level,
            "seismic_weight_kips": _r(s.weight_kips),
            "center_of_mass_ft": [_r(s.center_of_mass_ft[0]), _r(s.center_of_mass_ft[1])],
            "max_structure_depth_in": _r(depth),
            # ASSUMPTIONS OUT-1: story below minus the deepest framing and the plenum
            "clear_height_ft": _r(story_ft - (depth + inp.constraints.plenum_allowance_in) / 12),
        })

    columns, reactions = [], []
    for c in design.columns:
        stack = [{
            "level": s.story, "size": s.shape.name if s.shape else "none",
            "Pu_kips": _r(s.pu_kips), "Pd_kips": _r(s.pd_kips), "Pl_kips": _r(s.pl_kips),
            "Plr_kips": _r(s.plr_kips), "Psnow_kips": _r(s.ps_kips),
            "governing_combo": s.governing_combo,
        } for s in c.stories]
        columns.append({"id": c.column_id, "x_ft": _r(c.x_ft), "y_ft": _r(c.y_ft), "stack": stack})
        if c.stories:  # ASSUMPTIONS OUT-3: service loads of the bottom segment
            s = c.stories[0]
            reactions.append({"column_id": c.column_id, "D_kips": _r(s.pd_kips),
                              "L_kips": _r(s.pl_kips), "Lr_kips": _r(s.plr_kips),
                              "S_kips": _r(s.ps_kips)})

    framed = [b for b in cand.bays if b.framing]
    framing_tons = sum(b.framing.steel_psf * b.area_sf for b in framed) / 2000.0
    concrete_cy = sum(b.framing.concrete_cy_per_sf * b.area_sf for b in framed)
    area = cand.area_sf
    m = ev.metrics

    warnings = list(dict.fromkeys(input_warnings + cand.warnings + design.warnings))
    if cand.n_infeasible:
        warnings.append(f"{cand.n_infeasible} bay(s) have no framing in the bay study and are "
                        "left out of 'bays'; see the warnings above for each one")
    return {
        "system": system,
        "rank_score": _r(ev.score, 6),
        "grid": {"x_ft": [_r(x) for x in cand.layout.grid.x_ft],
                 "y_ft": [_r(y) for y in cand.layout.grid.y_ft]},
        "columns": columns,
        "bays": bays,
        "levels": level_rows,
        "foundation_reactions": reactions,
        "quantities": {"steel_tons": _r(framing_tons + design.tons), "concrete_cy": _r(concrete_cy),
                       "timber_cf": 0.0},
        "totals": {"cost_usd": _r(m.cost_usd, 0), "cost_per_sf": _r(m.cost_usd / area if area else 0.0),
                   "carbon_kgco2e": _r(m.carbon_kgco2e, 0),
                   "carbon_per_sf": _r(m.carbon_kgco2e / area if area else 0.0),
                   "max_depth_in": _r(m.max_depth_in)},
        "alternates": [],
        "warnings": warnings,
    }


def _empty_scheme(inp: GravityInput, system: str, warnings: list[str]) -> dict:
    """Something to draw when no grid could be built: the outline's bounding box, no framing."""
    x0, y0, x1, y1 = Polygon(inp.outline).bounds
    return {
        "system": system, "rank_score": 0.0,
        "grid": {"x_ft": [_r(x0), _r(x1)], "y_ft": [_r(y0), _r(y1)]},
        "columns": [], "bays": [], "levels": [], "foundation_reactions": [],
        "quantities": {"steel_tons": 0.0, "concrete_cy": 0.0, "timber_cf": 0.0},
        "totals": {"cost_usd": 0.0, "cost_per_sf": 0.0, "carbon_kgco2e": 0.0,
                   "carbon_per_sf": 0.0, "max_depth_in": 0.0},
        "alternates": [],
        "warnings": list(dict.fromkeys(warnings)),
    }


def _r(value: float, places: int = 3) -> float:
    return round(float(value), places)
