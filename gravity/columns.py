"""Tributary areas, gravity load accumulation, steel column sizing, and seismic weight.

Spec sections 4.5 and 3.2 (decisions log, spec section 7; docs/ASSUMPTIONS.md COL-*).
Steel only; concrete and mass timber come later.

Loads (per column, per framed level):
- Each bay's area is split equally to its four corner columns (the half-bay method on
  an orthogonal grid). The bay's governing occupancy applies to the whole bay.
- D = framing self-weight (DB steel_psf + slab/deck) + SDL + partitions (carried as dead)
  + perimeter cladding (config) + column self-weight (iterated).
- L is reduced per ASCE 7-22 4.7 with the cumulative AT of the reducible floors the
  column segment supports; Lr per 4.8 with the column's roof At; S is the flat roof snow.
- Pu = max of the ASCE 7-22 2.3.1 gravity combinations (``COMBOS``).

Steel sizing (AISC 360-22 E3, weak-axis flexural buckling, K = 1.0, L = story height):
KL/r = K L 12 / ry; Fe = pi^2 E / (KL/r)^2; Fcr = 0.658^(Fy/Fe) Fy if KL/r <= 4.71 sqrt(E/Fy),
else 0.877 Fe; phiPn = 0.90 Fcr A. Shapes slender for compression (Table B4.1a) are
excluded. Each tier of ``splice_every_n_levels`` stories gets the lightest W12/W14 whose
Pu/phiPn <= target_ratio in every story of the tier.
"""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from shapely.geometry import Point, Polygon, box

from gravity import db_adapter
from gravity.bays import CandidateResult
from gravity.grid import column_id
from gravity.loads import ROOF, ZoneMap, kll, occupancy, reduced_live_load, roof_live_load
from gravity.models import GravityInput

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
SHORT_TON_TO_TONNE = 0.90718474
TOL = 1e-6

# ASCE 7-22 2.3.1 gravity combinations: factors on (D, L, Lr, S).
COMBOS = {
    "1.4D": (1.4, 0.0, 0.0, 0.0),
    "1.2D+1.6L+0.5Lr": (1.2, 1.6, 0.5, 0.0),
    "1.2D+1.6L+0.5S": (1.2, 1.6, 0.0, 0.5),
    "1.2D+1.6Lr+1.0L": (1.2, 1.0, 1.6, 0.0),
    "1.2D+1.6S+1.0L": (1.2, 1.0, 0.0, 1.6),
}


@dataclass(frozen=True)
class Shape:
    name: str  # AISC_Manual_Label, e.g. "W12X53"
    weight_plf: float
    area_in2: float
    d_in: float
    bf_2tf: float
    h_tw: float
    rx_in: float
    ry_in: float


@dataclass(frozen=True)
class Capacity:
    length_ft: float
    kl_r: float
    fe_ksi: float
    fcr_ksi: float
    phi_pn_kips: float
    elastic: bool  # KL/r > 4.71 sqrt(E/Fy)

    def note(self, shape: Shape, k: float, fy: float) -> str:
        fcr = (f"0.877*Fe = {self.fcr_ksi:.2f} ksi" if self.elastic
               else f"0.658^({fy:g}/{self.fe_ksi:.2f})*{fy:g} = {self.fcr_ksi:.2f} ksi")
        return (
            f"{shape.name}: KL/r = {k:g}*{self.length_ft:g}*12/{shape.ry_in:g} = {self.kl_r:.2f}; "
            f"Fe = pi^2*E/(KL/r)^2 = {self.fe_ksi:.2f} ksi; Fcr = {fcr}; "
            f"phiPn = 0.90*{self.fcr_ksi:.2f}*{shape.area_in2:g} = {self.phi_pn_kips:.1f} kips"
        )


@dataclass
class LevelLoad:
    """Unfactored load one framed level delivers to one column."""

    level: str
    at_sf: float = 0.0
    d_kips: float = 0.0
    l_red_kips: float = 0.0  # reducible floor live load (Lo x area)
    l_nonred_kips: float = 0.0  # non-reducible floor live load
    lr0_kips: float = 0.0  # roof live load before reduction (Lo x area)
    s_kips: float = 0.0
    cladding_kips: float = 0.0  # also included in d_kips
    notes: list[str] = field(default_factory=list)


@dataclass
class StoryDesign:
    story: str  # level at the bottom of the story
    height_ft: float
    supports: list[str]  # framed levels whose load this segment carries
    pd_kips: float
    pl_kips: float  # reduced
    plr_kips: float  # reduced
    ps_kips: float
    pl_unreduced_kips: float
    self_weight_kips: float  # this segment and the segments above it
    ll_note: str
    lr_note: str
    combos: dict[str, float]
    pu_kips: float
    governing_combo: str
    shape: Shape | None = None
    capacity: Capacity | None = None
    ratio: float | None = None


@dataclass
class ColumnStack:
    column_id: str
    i: int
    j: int
    x_ft: float
    y_ft: float
    kind: str  # "interior", "edge" or "corner"
    kll: float
    level_loads: dict[str, LevelLoad]
    stories: list[StoryDesign]  # bottom to top
    warnings: list[str] = field(default_factory=list)

    @property
    def tons(self) -> float:
        return sum(s.shape.weight_plf * s.height_ft for s in self.stories if s.shape) / 2000.0


@dataclass
class ColumnDesign:
    columns: list[ColumnStack]
    cost_per_ton_usd: float
    carbon_kg_per_tonne: float
    warnings: list[str] = field(default_factory=list)

    @property
    def tons(self) -> float:
        return sum(c.tons for c in self.columns)

    @property
    def cost_usd(self) -> float:
        return self.tons * self.cost_per_ton_usd

    @property
    def carbon_kgco2e(self) -> float:
        return self.tons * SHORT_TON_TO_TONNE * self.carbon_kg_per_tonne

    @property
    def max_ratio(self) -> float:
        return max((s.ratio for c in self.columns for s in c.stories if s.ratio is not None),
                   default=0.0)

    def column(self, column_id_: str) -> ColumnStack:
        return next(c for c in self.columns if c.column_id == column_id_)


@dataclass
class LevelSeismic:
    level: str
    weight_kips: float
    center_of_mass_ft: tuple[float, float]


# ---------------------------------------------------------------------------
# Shapes and AISC capacity
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def load_shapes(path: Path, families: tuple[str, ...]) -> tuple[Shape, ...]:
    """W shapes of the given families from the AISC Shapes Database CSV export.

    The export repeats each column name (US units first, metric second), so columns are
    located by the FIRST occurrence of each name.
    """
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
    rows = csv.reader(io.StringIO(text))
    header = next(rows)
    idx: dict[str, int] = {}
    for i, name in enumerate(header):
        idx.setdefault(name.strip(), i)

    shapes = []
    for row in rows:
        if len(row) <= idx["ry"] or row[idx["Type"]].strip() != "W":
            continue
        name = row[idx["AISC_Manual_Label"]].strip()
        if not any(name.startswith(f"{family}X") for family in families):
            continue
        try:
            values = [float(row[idx[c]]) for c in ("W", "A", "d", "bf/2tf", "h/tw", "rx", "ry")]
        except ValueError:
            continue  # missing value ("-") in a needed column
        shapes.append(Shape(name, *values))
    return tuple(sorted(shapes, key=lambda s: (s.weight_plf, s.name)))


def is_slender(shape: Shape, fy: float, e: float) -> bool:
    """AISC 360-22 Table B4.1a, W shapes in axial compression."""
    root = math.sqrt(e / fy)
    return shape.bf_2tf > 0.56 * root or shape.h_tw > 1.49 * root


def compression_capacity(shape: Shape, length_ft: float, fy: float, e: float, k: float) -> Capacity:
    """AISC 360-22 E3, flexural buckling about the weak axis."""
    kl_r = k * length_ft * 12.0 / shape.ry_in
    fe = math.pi ** 2 * e / kl_r ** 2
    elastic = kl_r > 4.71 * math.sqrt(e / fy)
    fcr = 0.877 * fe if elastic else 0.658 ** (fy / fe) * fy
    return Capacity(length_ft, kl_r, fe, fcr, 0.90 * fcr * shape.area_in2, elastic)


def factored(d: float, l: float, lr: float, s: float) -> tuple[float, str, dict[str, float]]:
    """(Pu, governing combo, every combo) for ASCE 7-22 2.3.1."""
    values = {name: fd * d + fl * l + flr * lr + fs * s for name, (fd, fl, flr, fs) in COMBOS.items()}
    governing = max(values, key=lambda name: values[name])  # ties: first listed
    return values[governing], governing, values


# ---------------------------------------------------------------------------
# One column stack (pure: no DB, no geometry; used directly by the hand check)
# ---------------------------------------------------------------------------


def design_stack(
    stories: list[tuple[str, float]],
    supports: dict[str, list[str]],
    level_loads: dict[str, LevelLoad],
    kll_value: float,
    shapes: tuple[Shape, ...],
    target_ratio: float,
    splice_n: int,
    fy: float,
    e: float,
    k: float,
    max_iterations: int = 5,
) -> tuple[list[StoryDesign], list[str]]:
    """Loads, combinations and shapes for one column, stories listed bottom to top.

    Column self-weight is iterated: size without it, add the chosen shapes' weight, and
    resize until the shapes stop changing.
    """
    usable = [s for s in shapes if not is_slender(s, fy, e)]
    seg_weight = {name: 0.0 for name, _ in stories}
    warnings: list[str] = []
    for iteration in range(max_iterations):
        designs = [
            _story_loads(pos, stories, supports, level_loads, kll_value, seg_weight)
            for pos in range(len(stories))
        ]
        notes: list[str] = []
        for start in range(0, len(designs), splice_n):
            tier = designs[start:start + splice_n]
            shape, tier_notes = _select(usable, tier, target_ratio, fy, e, k)
            notes.extend(tier_notes)
            for d in tier:
                d.shape = shape
                d.capacity = compression_capacity(shape, d.height_ft, fy, e, k)
                d.ratio = d.pu_kips / d.capacity.phi_pn_kips
        new_weight = {d.story: d.shape.weight_plf * d.height_ft / 1000.0 for d in designs}
        if all(abs(new_weight[s] - seg_weight[s]) < 1e-9 for s in seg_weight):
            return designs, warnings + notes
        seg_weight = new_weight
    warnings.append(f"column self-weight did not converge in {max_iterations} iterations")
    return designs, warnings + notes


def _story_loads(pos, stories, supports, level_loads, kll_value, seg_weight) -> StoryDesign:
    story, height = stories[pos]
    levels = supports[story]
    loads = [level_loads[lv] for lv in levels if lv in level_loads]
    self_weight = sum(seg_weight[name] for name, _ in stories[pos:])

    pd = sum(x.d_kips for x in loads) + self_weight

    reducible = [x for x in loads if x.l_red_kips > 0]
    l_red = sum(x.l_red_kips for x in reducible)
    l_nonred = sum(x.l_nonred_kips for x in loads)
    if reducible:
        at = sum(x.at_sf for x in reducible)
        lo_avg = l_red * 1000.0 / at
        r = reduced_live_load(lo_avg, kll_value, at, len(reducible))
        pl_red = l_red * r.factor
        ll_note = f"reducible L {l_red:.2f} k over {len(reducible)} floor(s): {r.note}"
    else:
        pl_red, ll_note = 0.0, "no reducible floor live load"
    if l_nonred:
        ll_note += f"; plus non-reducible L {l_nonred:.2f} k"

    plr, lr_notes = 0.0, []
    for x in loads:
        if x.lr0_kips > 0:
            r = roof_live_load(x.lr0_kips * 1000.0 / x.at_sf, x.at_sf)
            plr += r.l_psf * x.at_sf / 1000.0
            lr_notes.append(f"{x.level}: {r.note} -> {r.l_psf * x.at_sf / 1000.0:.2f} k")

    ps = sum(x.s_kips for x in loads)
    pl = pl_red + l_nonred
    pu, combo, combos = factored(pd, pl, plr, ps)
    return StoryDesign(
        story=story, height_ft=height, supports=list(levels),
        pd_kips=pd, pl_kips=pl, plr_kips=plr, ps_kips=ps,
        pl_unreduced_kips=l_red + l_nonred, self_weight_kips=self_weight,
        ll_note=ll_note, lr_note="; ".join(lr_notes) or "no roof live load",
        combos=combos, pu_kips=pu, governing_combo=combo,
    )


def _select(usable, tier, target, fy, e, k) -> tuple[Shape, list[str]]:
    """Lightest shape with Pu/phiPn <= target in every story of the tier."""
    best, fallback = None, None
    for shape in usable:
        ratios = [d.pu_kips / compression_capacity(shape, d.height_ft, fy, e, k).phi_pn_kips
                  for d in tier]
        worst = max(ratios)
        if fallback is None or worst < fallback[0]:
            fallback = (worst, shape)
        if worst <= target + 1e-12:
            key = (shape.weight_plf, worst, shape.name)
            if best is None or key < best[0]:
                best = (key, shape)
    if best is not None:
        return best[1], []
    worst, shape = fallback
    stories = ", ".join(d.story for d in tier)
    return shape, [f"stories {stories}: no W12/W14 within ratio {target:g}; "
                   f"{shape.name} used at ratio {worst:.2f}"]


# ---------------------------------------------------------------------------
# All columns of a candidate
# ---------------------------------------------------------------------------


def column_config(system: str = "steel") -> dict[str, Any]:
    return _systems_config()["columns"][system]


def story_heights(inp: GravityInput) -> dict[str, float]:
    """Story height by its base level: that level's floor_to_floor_ft, else the elevation gap."""
    ordered = sorted(inp.levels, key=lambda lvl: lvl.elev_ft)
    return {
        a.name: a.floor_to_floor_ft if a.floor_to_floor_ft is not None else b.elev_ft - a.elev_ft
        for a, b in zip(ordered[:-1], ordered[1:])
    }


def design_columns(
    inp: GravityInput, zone_map: ZoneMap, candidate: CandidateResult, system: str = "steel"
) -> ColumnDesign:
    cfg = column_config(system)
    shapes = load_shapes(REPO_ROOT / cfg["shapes_csv"], tuple(cfg["families"]))
    lay = candidate.layout
    xs, ys = lay.grid.x_ft, lay.grid.y_ft
    outline = Polygon(inp.outline)
    levels = list(zone_map.levels)
    heights = story_heights(inp)
    warnings: list[str] = []

    contrib = _level_contributions(candidate, zone_map, system, warnings)
    _add_cladding(contrib, lay, levels, xs, ys, outline, cfg["cladding_plf"], warnings)

    columns = []
    points = sorted(frozenset().union(*lay.columns_by_story.values()), key=lambda p: (p[1], p[0]))
    for i, j in points:
        present = [s for s in levels[:-1] if (i, j) in lay.columns_by_story[s]]
        supports = {}
        for story in present:
            k_story = levels.index(story)
            supports[story] = [
                lv for m, lv in enumerate(levels) if m > k_story
                and (i, j) in lay.columns_by_story.get(levels[m - 1], frozenset())
            ]
        kind = _kind(i, j, xs, ys, outline)
        kll_value = kll(f"{kind}_column")
        level_loads = contrib.get((i, j), {})
        designs, notes = design_stack(
            [(s, heights[s]) for s in present], supports, level_loads, kll_value, shapes,
            cfg["target_ratio"], cfg["splice_every_n_levels"], cfg["fy_ksi"], cfg["e_ksi"],
            cfg["k"], cfg["max_self_weight_iterations"],
        )
        cid = column_id(i, j)
        columns.append(ColumnStack(cid, i, j, xs[i], ys[j], kind, kll_value, level_loads, designs,
                                   [f"Column {cid}: {n}" for n in notes]))

    rate = _costs()["steel"]["columns_usd_per_ton"]["value"]
    factor, unit = db_adapter.carbon_factor(cfg["carbon_material"])
    if unit.strip().lower() != "kg co2e/t":
        warnings.append(f"carbon factor unit {unit!r} not kg CO2e/t; column carbon may be wrong")
    for c in columns:
        warnings.extend(c.warnings)
    return ColumnDesign(columns, rate, factor, list(dict.fromkeys(warnings)))


def _level_contributions(candidate, zone_map, system, warnings):
    """(i, j) -> level -> LevelLoad, each bay's area split equally to its corners."""
    contrib: dict[tuple[int, int], dict[str, LevelLoad]] = {}
    for bay in candidate.bays:
        rect, loads = bay.rect, bay.loads
        if not rect.supported:
            warnings.append(f"{bay.level} bay {rect.bay_id}: unsupported; its load is not carried")
            continue
        occ = occupancy(loads.governing_occupancy)
        if bay.framing is not None:
            self_wt, wt_note = bay.framing.self_wt_psf, "framing"
        else:
            self_wt, wt_note = _fallback_self_weight(system, zone_map, bay.level), "slab/deck only"
            warnings.append(
                f"{bay.level} bay {rect.bay_id}: no framing; column dead load uses "
                f"slab/deck weight {self_wt:g} psf only"
            )
        d_psf = self_wt + loads.sdl_psf + loads.partitions_psf
        quarter = bay.area_sf / 4.0
        roof_live = zone_map.levels[bay.level].role == ROOF and occ.is_roof
        for p in ((rect.i0, rect.j0), (rect.i1 + 1, rect.j0), (rect.i0, rect.j1 + 1),
                  (rect.i1 + 1, rect.j1 + 1)):
            x = contrib.setdefault(p, {}).setdefault(bay.level, LevelLoad(bay.level))
            x.at_sf += quarter
            x.d_kips += quarter * d_psf / 1000.0
            if roof_live:
                x.lr0_kips += quarter * loads.ll_psf / 1000.0
            elif loads.ll_reducible and loads.ll_psf <= 100:
                x.l_red_kips += quarter * loads.ll_psf / 1000.0
            else:
                x.l_nonred_kips += quarter * loads.ll_psf / 1000.0
            if zone_map.levels[bay.level].role == ROOF:
                x.s_kips += quarter * zone_map.snow_psf / 1000.0
            x.notes.append(
                f"{rect.bay_id} ({loads.governing_occupancy}): {quarter:g} sf x D "
                f"({wt_note} {self_wt:g} + SDL {loads.sdl_psf:g} + partitions "
                f"{loads.partitions_psf:g} = {d_psf:g} psf), "
                f"{'Lr0' if roof_live else 'L'} {loads.ll_psf:g} psf"
            )
    return contrib


def _add_cladding(contrib, lay, levels, xs, ys, outline, plf, warnings):
    if not plf:
        warnings.append("Perimeter cladding load taken as 0 (config/systems.json cladding_plf)")
        return
    boundary = outline.exterior
    for level in lay.bays_by_level:
        support = lay.columns_by_story[levels[levels.index(level) - 1]]
        for i, j in support:
            length = 0.0
            for ni, nj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
                if 0 <= ni < len(xs) and 0 <= nj < len(ys):
                    mid = Point((xs[i] + xs[ni]) / 2, (ys[j] + ys[nj]) / 2)
                    if boundary.distance(mid) < TOL:
                        length += math.hypot(xs[ni] - xs[i], ys[nj] - ys[j]) / 2
            if length:
                x = contrib.setdefault((i, j), {}).setdefault(level, LevelLoad(level))
                x.d_kips += plf * length / 1000.0
                x.cladding_kips += plf * length / 1000.0
                x.notes.append(f"cladding {plf:g} plf x {length:g} ft")


def _kind(i, j, xs, ys, outline) -> str:
    """By framed cells around the column: 4 interior, 1 corner, otherwise edge."""
    n = 0
    for ci in (i - 1, i):
        for cj in (j - 1, j):
            if 0 <= ci < len(xs) - 1 and 0 <= cj < len(ys) - 1:
                if outline.covers(box(xs[ci], ys[cj], xs[ci + 1], ys[cj + 1]).centroid):
                    n += 1
    return "interior" if n == 4 else "corner" if n <= 1 else "edge"


def _fallback_self_weight(system, zone_map, level) -> float:
    use = "roof" if zone_map.levels[level].role == ROOF else "floor"
    db_systems = _systems_config()["systems"][system][use]["db_systems"]
    weights = [_systems_config()["db_systems"][s]["slab_deck_psf"] for s in db_systems]
    return max((w for w in weights if w is not None), default=0.0)


# ---------------------------------------------------------------------------
# Seismic weight per level (spec 3.2; ASCE 7-22 12.7.2)
# ---------------------------------------------------------------------------


def seismic_weights(
    inp: GravityInput, zone_map: ZoneMap, candidate: CandidateResult, design: ColumnDesign,
    system: str = "steel",
) -> list[LevelSeismic]:
    """D + partitions + 25% of storage LL + 20% of flat roof snow over 30 psf, plus half
    the columns above and below each level and the perimeter cladding."""
    levels = list(zone_map.levels)
    seg = {(c.column_id, s.story): (c, s) for c in design.columns for s in c.stories if s.shape}
    results = []
    for level in candidate.layout.bays_by_level:
        items: list[tuple[float, float, float]] = []
        role = zone_map.levels[level].role
        for bay in (b for b in candidate.bays if b.level == level):
            occ = occupancy(bay.loads.governing_occupancy)
            self_wt = (bay.framing.self_wt_psf if bay.framing
                       else _fallback_self_weight(system, zone_map, level))
            w_psf = self_wt + bay.loads.sdl_psf + bay.loads.partitions_psf
            if occ.seismic_storage:
                w_psf += 0.25 * bay.loads.ll_psf
            if role == ROOF and zone_map.snow_psf > 30:
                w_psf += 0.20 * zone_map.snow_psf
            c = bay.rect.polygon.centroid
            items.append((w_psf * bay.area_sf / 1000.0, c.x, c.y))
        k = levels.index(level)
        for story in (levels[k - 1], level):  # half of the story below and the story above
            for c in design.columns:
                if (c.column_id, story) in seg:
                    _, s = seg[(c.column_id, story)]
                    items.append((s.shape.weight_plf * s.height_ft / 2000.0, c.x_ft, c.y_ft))
        for c in design.columns:
            x = c.level_loads.get(level)
            if x is not None and x.cladding_kips:
                items.append((x.cladding_kips, c.x_ft, c.y_ft))
        total = sum(w for w, _, _ in items)
        com = ((sum(w * x for w, x, _ in items) / total, sum(w * y for w, _, y in items) / total)
               if total else (0.0, 0.0))
        results.append(LevelSeismic(level, total, com))
    return results


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _config(name: str) -> dict[str, Any]:
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


def _systems_config() -> dict[str, Any]:
    return _config("systems.json")


def _costs() -> dict[str, Any]:
    return _config("costs.json")
