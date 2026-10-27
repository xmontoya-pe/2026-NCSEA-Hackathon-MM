"""Per-system search loop, and the output scheme for each result.

Spec section 4.7. For each requested system: generate grids (``grid``), evaluate bays
(``bays``), size columns (``columns``), score (``scoring``), and return the best scheme
plus the top 3 alternates. Deterministic: ties sort by fewest columns, then grid id.

An output system (``steel``, ``concrete``, ``mass_timber``) is a *family* of one or more
structural systems in config/systems.json. Each structural system is used uniformly over
the whole building (no mixing concrete systems bay by bay; decisions log). Every
candidate of every structural system in the family is scored together, and the best is
the scheme; its alternates may be other structural systems of the same family.

``scheme`` turns one evaluated candidate into the spec 3.1 scheme dict
(docs/ASSUMPTIONS.md O-*).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gravity import db_adapter, scoring
from gravity.bays import CandidateResult, evaluate
from gravity.columns import ColumnDesign, design_columns, seismic_weights, story_heights
from gravity.grid import generate_grids, layout
from gravity.loads import ZoneMap, resolve_zones
from gravity.models import GravityInput

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
N_ALTERNATES = 3
LB_PER_TON = 2000.0


@dataclass
class Evaluated:
    """One candidate grid with its framing, columns and score."""

    candidate: CandidateResult
    design: ColumnDesign
    score: float = 0.0  # weighted, 0..1 (scoring.weighted_scores)
    rank_score: float = 0.0  # score + penalties; lower is better
    system: str = "steel"  # structural system, e.g. "concrete_pt_fp"

    @property
    def grid_id(self) -> str:
        return self.candidate.grid_id

    @property
    def label(self) -> str:
        return db_adapter.label(self.system)

    @property
    def area_sf(self) -> float:
        return self.candidate.area_sf

    @property
    def framing_tons(self) -> float:
        bays = self.candidate.bays
        return sum(b.framing.steel_psf * b.area_sf for b in bays if b.framing) / LB_PER_TON

    @property
    def column_tons(self) -> float:
        return self.design.tons

    @property
    def concrete_cy(self) -> float:
        bays = self.candidate.bays
        return (sum(b.framing.concrete_cy_per_sf * b.area_sf for b in bays if b.framing)
                + self.design.concrete_cy)

    @property
    def timber_cf(self) -> float:
        bays = self.candidate.bays
        return (sum(b.framing.timber_cf_per_sf * b.area_sf for b in bays if b.framing)
                + self.design.timber_cf)

    @property
    def cost_usd(self) -> float:
        return self.candidate.framing_cost_usd + self.design.cost_usd

    @property
    def carbon_kgco2e(self) -> float:
        return self.candidate.framing_carbon_kgco2e + self.design.carbon_kgco2e

    @property
    def max_depth_in(self) -> float:
        return self.candidate.max_depth_in

    def rank_key(self) -> tuple:
        return (round(self.rank_score, 9), self.candidate.layout.column_count, self.grid_id,
                self.system)


@dataclass
class SystemResult:
    system: str
    zone_map: ZoneMap
    ranked: list[Evaluated]  # best first
    warnings: list[str] = field(default_factory=list)  # input-level (zones, grid generation)


def is_built(system: str) -> bool:
    """Output systems with at least one structural system and column sizing in config."""
    cfg = json.loads((CONFIG_DIR / "systems.json").read_text(encoding="utf-8"))
    return bool(db_adapter.variants(system)) and system in cfg["columns"]


def optimize(inp: GravityInput, system: str = "steel") -> SystemResult:
    """Every candidate grid of every structural system in one family, evaluated, sized,
    scored together and ranked."""
    zone_map = resolve_zones(inp)
    evaluated = []
    grid_warnings: list[str] = []
    for variant in db_adapter.variants(system):
        grids, warnings = generate_grids(inp, system=variant)
        grid_warnings += [w for w in warnings if w not in grid_warnings]
        cache: dict = {}
        for g in grids:
            candidate = evaluate(inp, zone_map, layout(inp, zone_map, g), variant, cache)
            design = design_columns(inp, zone_map, candidate, variant)
            evaluated.append(Evaluated(candidate, design, system=variant))

    scores = scoring.weighted_scores(
        [{"cost": e.cost_usd, "depth": e.max_depth_in, "carbon": e.carbon_kgco2e}
         for e in evaluated],
        inp.objective_weights,
    )
    for e, s in zip(evaluated, scores):
        e.score = s
        e.rank_score = s + scoring.penalty(e.candidate.n_infeasible, e.candidate.n_over_depth)
    evaluated.sort(key=Evaluated.rank_key)
    return SystemResult(system, zone_map, evaluated, zone_map.warnings + grid_warnings)


# ---------------------------------------------------------------------------
# Output scheme (spec 3.1)
# ---------------------------------------------------------------------------


def schemes_for(inp: GravityInput, result: SystemResult, extra_warnings: list[str]) -> dict:
    """The best candidate as a scheme dict, with up to N_ALTERNATES runner-ups."""
    best, *rest = result.ranked
    out = scheme(inp, result, best, extra_warnings)
    out["alternates"] = [scheme(inp, result, e, extra_warnings) for e in rest[:N_ALTERNATES]]
    return out


def scheme(inp: GravityInput, result: SystemResult, e: Evaluated,
           extra_warnings: list[str]) -> dict[str, Any]:
    system, zone_map = result.system, result.zone_map
    cand, design = e.candidate, e.design
    grid = cand.layout.grid

    columns, reactions = [], []
    for c in design.columns:
        stack = [
            {"level": s.story, "size": s.shape.name, "Pu_kips": _r(s.pu_kips),
             "Pd_kips": _r(s.pd_kips), "Pl_kips": _r(s.pl_kips), "Plr_kips": _r(s.plr_kips),
             "Psnow_kips": _r(s.ps_kips), "governing_combo": s.governing_combo}
            for s in c.stories
        ]
        columns.append({"id": c.column_id, "x_ft": c.x_ft, "y_ft": c.y_ft, "stack": stack})
        base = c.stories[0]  # ASSUMPTIONS O-3
        reactions.append({"column_id": c.column_id, "D_kips": _r(base.pd_kips),
                          "L_kips": _r(base.pl_kips), "Lr_kips": _r(base.plr_kips),
                          "S_kips": _r(base.ps_kips)})

    bays = [
        {"id": b.rect.bay_id, "level": b.level, "span_x_ft": b.rect.span_x_ft,
         "span_y_ft": b.rect.span_y_ft, "governing_occupancy": b.loads.governing_occupancy,
         "ll_psf": b.loads.ll_psf, "sdl_psf": b.loads.sdl_psf,
         "framing": {"slab": b.framing.slab, "beam": b.framing.beam, "girder": b.framing.girder,
                     "depth_in": b.framing.depth_in, "self_wt_psf": _r(b.framing.self_wt_psf),
                     "db_row": b.framing.db_row}}
        for b in cand.bays if b.framing is not None  # ASSUMPTIONS O-1
    ]

    names = list(zone_map.levels)
    heights = story_heights(inp)
    seismic = {s.level: s for s in seismic_weights(inp, zone_map, cand, design, e.system)}
    levels = []
    for level in cand.layout.bays_by_level:
        depth = max((b.framing.depth_in for b in cand.bays if b.level == level and b.framing),
                    default=0.0)
        below = names[names.index(level) - 1]
        clear = heights[below] - (depth + inp.constraints.plenum_allowance_in) / 12.0  # O-2
        s = seismic[level]
        levels.append({"level": level, "seismic_weight_kips": _r(s.weight_kips),
                       "center_of_mass_ft": [_r(s.center_of_mass_ft[0]),
                                             _r(s.center_of_mass_ft[1])],
                       "max_structure_depth_in": depth, "clear_height_ft": _r(clear)})

    area = e.area_sf or 1.0
    identity = []
    if len(db_adapter.variants(system)) > 1 or e.system != system:  # ASSUMPTIONS O-7
        floor_db = ", ".join(_db_names(e.system))
        identity = [f"{system.replace('_', ' ').capitalize()} system: {e.label} ({floor_db})"]
    warnings = list(dict.fromkeys(
        identity + result.warnings + cand.warnings + design.warnings + extra_warnings
    ))
    return {
        "system": system,
        "rank_score": _r(e.rank_score, 4),
        "grid": {"x_ft": list(grid.x_ft), "y_ft": list(grid.y_ft)},
        "columns": columns,
        "bays": bays,
        "levels": levels,
        "foundation_reactions": reactions,
        "quantities": {"steel_tons": _r(e.framing_tons + e.column_tons),
                       "concrete_cy": _r(e.concrete_cy), "timber_cf": _r(e.timber_cf)},
        "totals": {"cost_usd": _r(e.cost_usd, 0), "cost_per_sf": _r(e.cost_usd / area, 2),
                   "carbon_kgco2e": _r(e.carbon_kgco2e, 0),
                   "carbon_per_sf": _r(e.carbon_kgco2e / area, 2),
                   "max_depth_in": e.max_depth_in},
        "alternates": [],
        "warnings": warnings,
    }


def _db_names(system: str) -> list[str]:
    cfg = json.loads((CONFIG_DIR / "systems.json").read_text(encoding="utf-8"))["systems"][system]
    return list(dict.fromkeys(cfg["floor"]["db_systems"] + cfg["roof"]["db_systems"]))


def _r(value: float, digits: int = 3) -> float:
    return round(float(value), digits)
