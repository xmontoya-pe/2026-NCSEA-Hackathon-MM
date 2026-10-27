"""Bay framing lookup for each bay of a candidate grid, plus the depth constraint.

Spec sections 4.3 and 4.4 (decisions log, spec section 7; docs/ASSUMPTIONS.md B-*). Works
for any structural system in config/systems.json (steel, the concrete systems, mass timber).

For each framed level and bay of a ``grid.GridLayout``:

- Loads come from ``loads.bay_loads`` (governing occupancy, lookup LL, SDL, criterion).
- ``db_adapter.framing_options`` returns every DB option, both beam orientations, with
  spans and loads rounded up. Options within the allowable depth are preferred; among
  them the one with the best weighted score (``objective_weights`` over cost, depth and
  carbon, min-max normalized across that bay's options) is kept.
- A bay with no DB option is marked infeasible with a warning; the candidate is
  penalized (ranked after every feasible candidate) rather than dropped.
- ``allowable_depth_in = (floor_to_floor_ft - min_ceiling_height_ft)*12 -
  plenum_allowance_in`` using the story BELOW the framed level, overridden by
  ``max_structure_depth_in``. Exceeding it penalizes the candidate, never removes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gravity import db_adapter
from gravity.db_adapter import BayFraming
from gravity.grid import BayRect, GridLayout, generate_grids, layout
from gravity.loads import ROOF, BayLoads, ZoneMap, bay_loads, resolve_zones
from gravity.models import GravityInput

DEPTH_TOL_IN = 1e-6


@dataclass
class BayResult:
    level: str
    rect: BayRect
    loads: BayLoads
    framing: BayFraming | None  # None: infeasible
    allowable_depth_in: float
    area_sf: float
    warnings: list[str] = field(default_factory=list[str])

    @property
    def feasible(self) -> bool:
        return self.framing is not None

    @property
    def depth_ok(self) -> bool:
        return self.framing is not None and (
            self.framing.depth_in <= self.allowable_depth_in + DEPTH_TOL_IN
        )

    @property
    def over_depth_in(self) -> float:
        """Inches the framing exceeds the allowable depth (0 if within it or infeasible)."""
        if self.framing is None or self.depth_ok:
            return 0.0
        return self.framing.depth_in - self.allowable_depth_in


@dataclass
class CandidateResult:
    layout: GridLayout
    bays: list[BayResult]

    @property
    def grid_id(self) -> str:
        return self.layout.grid.grid_id

    @property
    def area_sf(self) -> float:
        return sum(b.area_sf for b in self.bays)

    @property
    def framing_cost_usd(self) -> float:
        """Floor and roof framing only (columns are added by columns.py)."""
        return sum(b.framing.cost_per_sf * b.area_sf for b in self.bays if b.framing)

    @property
    def framing_carbon_kgco2e(self) -> float:
        return sum(b.framing.carbon_kgco2e_per_sf * b.area_sf for b in self.bays if b.framing)

    @property
    def max_depth_in(self) -> float:
        return max((b.framing.depth_in for b in self.bays if b.framing), default=0.0)

    @property
    def n_infeasible(self) -> int:
        return sum(not b.feasible for b in self.bays)

    @property
    def n_over_depth(self) -> int:
        return sum(b.feasible and not b.depth_ok for b in self.bays)

    @property
    def over_depth_in(self) -> float:
        """Sum over bays of the inches each exceeds its allowable depth."""
        return sum(b.over_depth_in for b in self.bays)

    @property
    def warnings(self) -> list[str]:
        """Layout warnings, then each bay's warnings, without duplicates."""
        seen: dict[str, None] = dict.fromkeys(self.layout.warnings)
        for b in self.bays:
            seen.update(dict.fromkeys(b.warnings))
        return list(seen)

    def rank_key(self) -> tuple:
        """Feasible first, then within depth, then cheapest; ties per spec 4.7."""
        return (self.n_infeasible, self.n_over_depth, round(self.framing_cost_usd, 2),
                self.layout.column_count, self.grid_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def allowable_depth_in(inp: GravityInput, level: str) -> float:
    """Spec 4.4, using the story below ``level`` (decisions log)."""
    c = inp.constraints
    if c.max_structure_depth_in is not None:
        return c.max_structure_depth_in
    ordered = sorted(inp.levels, key=lambda lvl: lvl.elev_ft)
    names = [lvl.name for lvl in ordered]
    k = names.index(level)
    if k == 0:
        raise ValueError(f"level {level!r} has no story below it")
    below, here = ordered[k - 1], ordered[k]
    story_ft = below.floor_to_floor_ft
    if story_ft is None:
        story_ft = here.elev_ft - below.elev_ft
    return (story_ft - c.min_ceiling_height_ft) * 12 - c.plenum_allowance_in


def evaluate(
    inp: GravityInput, zone_map: ZoneMap, grid_layout: GridLayout, system: str = "steel",
    cache: dict | None = None,
) -> CandidateResult:
    """Framing for every bay of one grid layout."""
    cache = {} if cache is None else cache
    results = []
    for level, rects in grid_layout.bays_by_level.items():
        allow = allowable_depth_in(inp, level)
        use = "roof" if zone_map.levels[level].role == ROOF else "floor"
        for rect in rects:
            results.append(_evaluate_bay(inp, zone_map, level,
                           use, rect, allow, system, cache))
    return CandidateResult(grid_layout, results)


def evaluate_all(
    inp: GravityInput, system: str = "steel"
) -> tuple[list[CandidateResult], list[str]]:
    """Every candidate grid, best first (rank_key), and the input-level warnings."""
    zone_map = resolve_zones(inp)
    grids, grid_warnings = generate_grids(inp, system=system)
    cache: dict = {}
    results = [evaluate(inp, zone_map, layout(
        inp, zone_map, g), system, cache) for g in grids]
    return sorted(results, key=CandidateResult.rank_key), zone_map.warnings + grid_warnings


# ---------------------------------------------------------------------------
# Per bay
# ---------------------------------------------------------------------------


def _evaluate_bay(inp, zone_map, level, use, rect, allow, system, cache) -> BayResult:
    loads = bay_loads(zone_map, level, rect.polygon, db_adapter.family(system))
    area = rect.polygon.area
    tag = f"{level} bay {rect.bay_id}"
    warnings = [f"{tag}: {w}" for w in loads.warnings]

    if not rect.supported:
        warnings.append(f"{tag}: no column arrangement supports this bay")
        return BayResult(level, rect, loads, None, allow, area, warnings)

    criterion = loads.criterion if use == "floor" else None
    key = (system, use, rect.span_x_ft, rect.span_y_ft, loads.lookup_ll_psf, loads.sdl_psf,
           criterion)
    if key not in cache:
        cache[key] = db_adapter.framing_options(
            system, use, rect.span_x_ft, rect.span_y_ft, loads.lookup_ll_psf, loads.sdl_psf,
            criterion,
        )
    options = cache[key]

    if not options:
        warnings.append(
            f"{tag}: {_infeasible_reason(system, use, rect, loads)}")
        return BayResult(level, rect, loads, None, allow, area, warnings)

    framing = _choose(options, allow, inp.objective_weights)
    warnings.extend(f"{tag}: {w}" for w in framing.warnings)
    if framing.depth_in > allow + DEPTH_TOL_IN:
        warnings.append(
            f"{tag}: shallowest option is {framing.depth_in:g} in, over the "
            f"{allow:g} in allowable"
        )
    return BayResult(level, rect, loads, framing, allow, area, warnings)


def _choose(options: list[BayFraming], allow: float, weights) -> BayFraming:
    """Prefer options within depth; then best weighted score (spec 4.6 weights)."""
    within = [o for o in options if o.depth_in <= allow + DEPTH_TOL_IN]
    if not within:
        return min(options, key=lambda o: (o.depth_in, o.cost_per_sf, o.db_row))
    metrics = {
        "cost": [o.cost_per_sf for o in within],
        "depth": [o.depth_in for o in within],
        "carbon": [o.carbon_kgco2e_per_sf or 0.0 for o in within],
    }

    def norm(name, value):
        lo, hi = min(metrics[name]), max(metrics[name])
        return 0.0 if hi - lo < 1e-12 else (value - lo) / (hi - lo)

    def score(o):
        return (
            weights.cost * norm("cost", o.cost_per_sf)
            + weights.depth * norm("depth", o.depth_in)
            + weights.carbon * norm("carbon", o.carbon_kgco2e_per_sf or 0.0)
        )

    # options arrive sorted cheapest first, so ties keep the cheaper, lower-row option
    return min(within, key=score)


def _infeasible_reason(system, use, rect, loads: BayLoads) -> str:
    beams, girders = db_adapter.available_spans(system, use)
    lls, _ = db_adapter.available_loads(system, use)
    name = system.replace("_", " ")
    sx, sy = rect.span_x_ft, rect.span_y_ft
    fits = (sx <= max(beams) and sy <= max(girders)) or (
        sy <= max(beams) and sx <= max(girders))
    spans = f"{sx:g} x {sy:g} ft"
    if not fits:
        return (
            f"{spans} exceeds the {name} bay study (span 1 <= {max(beams):g} ft, span 2 <= "
            f"{max(girders):g} ft); long-span or transfer framing needed"
        )
    if loads.lookup_ll_psf > max(lls):
        return (
            f"LL {loads.lookup_ll_psf:g} psf ({loads.governing_occupancy}) exceeds the {name} "
            f"bay study (max {max(lls):g} psf); infeasible"
        )
    return f"no solved {name} design for {spans} at LL {loads.lookup_ll_psf:g} psf; infeasible"
