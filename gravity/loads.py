"""Occupancy -> LL/SDL, zone-to-bay mapping, and live load reduction.

Spec section 4.1, with inputs from 2.1 (zone notes) and 2.2 (config files). Rules
(decisions log, spec section 7; docs/ASSUMPTIONS.md):

- Levels: the lowest level is slab-on-grade (not framed), the highest is the roof,
  everything between is a framed floor.
- Typical zones (``typical_zones_apply_to_all_levels``) copy between framed floors only.
  An explicit zone on a level replaces copied zones only where it overlaps them.
  Copies carry the occupancy, not ``no_columns`` (ASSUMPTIONS Z-1).
- The roof is ``roof`` occupancy except where an explicit roof zone says otherwise.
- Floor area covered by no zone gets ``default_occupancy`` and a warning.
- A bay's governing load is the max lookup LL (LL + partitions) of any zone it overlaps;
  area fractions are kept. Bay lookups use unreduced LL.
- LL reduction (ASCE 7-22 4.7) and roof Lr reduction (4.8) are provided for column
  loads; each result carries a note showing the arithmetic.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from gravity.models import GravityInput

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

SLAB_ON_GRADE = "slab_on_grade"
FLOOR = "floor"
ROOF = "roof"

# Pieces of zones smaller than this (sq ft) are geometric slivers and are ignored.
AREA_TOL_SF = 1.0


@dataclass(frozen=True)
class Occupancy:
    key: str
    ll_psf: float
    partitions_psf: float
    ll_reducible: bool
    criterion: str | None
    is_roof: bool = False
    seismic_storage: bool = False  # 25% of LL counts in seismic weight (ASCE 7-22 12.7.2)

    @property
    def lookup_ll_psf(self) -> float:
        """LL used for bay lookups: LL + partitions (ASSUMPTIONS L-2)."""
        return self.ll_psf + self.partitions_psf


@dataclass
class ResolvedZone:
    zone_id: str  # input zone id, or "default" / "roof" for generated zones
    occupancy: Occupancy
    geometry: BaseGeometry  # clipped to the outline
    copied_from: str | None = None  # source level for a typical-zone copy


@dataclass
class LevelZones:
    level: str
    role: str  # SLAB_ON_GRADE, FLOOR or ROOF
    zones: list[ResolvedZone]  # occupancy zones covering the plate (none on slab-on-grade)
    no_column_zones: list[ResolvedZone]  # govern the story from this level up


@dataclass
class ZoneMap:
    levels: dict[str, LevelZones]  # ordered from lowest to highest
    snow_psf: float
    warnings: list[str] = field(default_factory=list)


@dataclass
class BayLoads:
    level: str
    governing_occupancy: str
    ll_psf: float  # governing occupancy's LL (unreduced)
    partitions_psf: float
    lookup_ll_psf: float  # LL + partitions, or max(Lr, S) on the roof
    sdl_psf: float
    criterion: str | None  # bay study vibration criterion, None = Strength
    ll_reducible: bool  # every overlapping occupancy is reducible
    area_fractions: dict[str, float]  # occupancy key -> fraction of bay area
    zone_ids: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReducedLiveLoad:
    lo_psf: float
    l_psf: float
    factor: float  # l_psf / lo_psf
    note: str  # the arithmetic, for "show your work"


# ---------------------------------------------------------------------------
# Occupancies and config
# ---------------------------------------------------------------------------


def occupancy(key: str, warnings: list[str] | None = None) -> Occupancy:
    """Occupancy from config. An unknown key falls back to the default, with a warning."""
    table = _occupancies()["occupancies"]
    if key not in table:
        default = _settings()["default_occupancy"]
        if warnings is not None:
            warnings.append(f"Unknown occupancy {key!r}; default occupancy {default!r} used")
        key = default
    entry = table[key]
    return Occupancy(
        key=key,
        ll_psf=float(entry["ll_psf"]),
        partitions_psf=float(entry["partitions_psf"]),
        ll_reducible=bool(entry["ll_reducible"]),
        criterion=entry.get("criterion"),
        is_roof=bool(entry.get("is_roof", False)),
        seismic_storage=bool(entry.get("seismic_storage", False)),
    )


def kll(member: str) -> float:
    """Live load element factor KLL (ASCE 7-22 Table 4.7-1) from config."""
    return float(_settings()["kll"][member])


def sdl_psf(system: str, role: str) -> float:
    return float(_config("sdl.json")[system][f"{role}_psf"])


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


def level_roles(inp: GravityInput) -> dict[str, str]:
    """Level name -> role, ordered from lowest to highest elevation."""
    ordered = sorted(inp.levels, key=lambda lvl: lvl.elev_ft)
    roles = {lvl.name: FLOOR for lvl in ordered}
    roles[ordered[0].name] = SLAB_ON_GRADE
    if len(ordered) > 1:
        roles[ordered[-1].name] = ROOF
    return roles


def resolve_zones(inp: GravityInput) -> ZoneMap:
    warnings: list[str] = []
    outline = Polygon(inp.outline)
    roles = level_roles(inp)

    explicit: dict[str, list[ResolvedZone]] = {name: [] for name in roles}
    no_columns: dict[str, list[ResolvedZone]] = {name: [] for name in roles}
    for z in inp.zones:
        geometry = Polygon(z.polygon).intersection(outline)
        if geometry.area < AREA_TOL_SF:
            warnings.append(f"Zone {z.id} on {z.level} lies outside the building outline; ignored")
            continue
        zone = ResolvedZone(z.id, occupancy(z.occupancy, warnings), geometry)
        explicit[z.level].append(zone)
        if z.no_columns:
            if roles[z.level] == ROOF:
                warnings.append(
                    f"Zone {z.id}: no_columns on the roof level has no story above it; ignored"
                )
            else:
                no_columns[z.level].append(zone)

    floors = [name for name, role in roles.items() if role == FLOOR]
    default_key = _settings()["default_occupancy"]
    levels: dict[str, LevelZones] = {}
    for name, role in roles.items():
        zones = list(explicit[name])
        if role == FLOOR and inp.typical_zones_apply_to_all_levels:
            own = _union(explicit[name])
            for source in floors:
                if source == name:
                    continue
                for z in explicit[source]:
                    geometry = z.geometry if own is None else z.geometry.difference(own)
                    if geometry.area >= AREA_TOL_SF:
                        zones.append(ResolvedZone(z.zone_id, z.occupancy, geometry, source))

        if role in (FLOOR, ROOF):
            covered = _union(zones)
            rest = outline if covered is None else outline.difference(covered)
            if rest.area >= AREA_TOL_SF:
                if role == ROOF:
                    zones.append(ResolvedZone("roof", occupancy("roof"), rest))
                else:
                    zones.append(ResolvedZone("default", occupancy(default_key), rest))
                    warnings.append(
                        f"Level {name}: {rest.area:,.0f} sq ft is not covered by any zone; "
                        f"default occupancy '{default_key}' applied"
                    )
        levels[name] = LevelZones(name, role, zones if role != SLAB_ON_GRADE else [],
                                  no_columns[name])

    snow = _settings().get("flat_roof_snow_psf")
    if snow is None:
        snow = 0.0
        if ROOF in roles.values():
            warnings.append(
                "No flat roof snow load given; taken as 0 psf, so roof lookups use Lr"
            )
    return ZoneMap(levels, float(snow), warnings)


def bay_loads(
    zone_map: ZoneMap, level: str, bay: BaseGeometry | Sequence[Sequence[float]],
    system: str = "steel",
) -> BayLoads:
    """Loads for one bay polygon on a framed level (spec 4.1)."""
    if level not in zone_map.levels:
        raise ValueError(f"unknown level {level!r}")
    level_zones = zone_map.levels[level]
    if level_zones.role == SLAB_ON_GRADE:
        raise ValueError(f"level {level!r} is slab-on-grade and has no framing")
    geometry = bay if isinstance(bay, BaseGeometry) else Polygon(bay)
    area = geometry.area
    warnings: list[str] = []

    by_occupancy: dict[str, list[ResolvedZone]] = {}
    for z in level_zones.zones:
        by_occupancy.setdefault(z.occupancy.key, []).append(z)

    fractions: dict[str, float] = {}
    occupancies: dict[str, Occupancy] = {}
    zone_ids: set[str] = set()
    for key, zones in by_occupancy.items():
        overlap = _union(zones).intersection(geometry).area
        if overlap < AREA_TOL_SF:
            continue
        fractions[key] = overlap / area
        occupancies[key] = zones[0].occupancy
        zone_ids.update(z.zone_id for z in zones if z.geometry.intersection(geometry).area
                        >= AREA_TOL_SF)

    if not occupancies:
        default = occupancy(_settings()["default_occupancy"])
        warnings.append(f"Bay on {level} overlaps no zone; default occupancy '{default.key}' used")
        occupancies = {default.key: default}
        fractions = {default.key: 1.0}

    governing = max(
        occupancies.values(),
        key=lambda o: (o.lookup_ll_psf, fractions[o.key], o.ll_psf, o.key),
    )
    lookup = governing.lookup_ll_psf
    if level_zones.role == ROOF:
        lookup = max(lookup, zone_map.snow_psf)  # ASSUMPTIONS L-5

    return BayLoads(
        level=level,
        governing_occupancy=governing.key,
        ll_psf=governing.ll_psf,
        partitions_psf=governing.partitions_psf,
        lookup_ll_psf=lookup,
        sdl_psf=sdl_psf(system, level_zones.role),
        criterion=_most_stringent(o.criterion for o in occupancies.values()),
        ll_reducible=all(o.ll_reducible for o in occupancies.values()),
        area_fractions=dict(sorted(fractions.items())),
        zone_ids=sorted(zone_ids),
        warnings=warnings,
    )


def no_column_zones(zone_map: ZoneMap, level: str) -> list[ResolvedZone]:
    """Zones where the story from ``level`` up must stay column-free."""
    return zone_map.levels[level].no_column_zones


# ---------------------------------------------------------------------------
# Live load reduction (for column loads; bay lookups use unreduced LL)
# ---------------------------------------------------------------------------


def reduced_live_load(
    lo_psf: float, kll_value: float, at_sf: float, floors_supported: int, reducible: bool = True
) -> ReducedLiveLoad:
    """ASCE 7-22 Eq. 4.7-1: L = Lo (0.25 + 15 / sqrt(KLL AT)), with the 0.5Lo / 0.4Lo limits."""
    if not reducible:
        return ReducedLiveLoad(lo_psf, lo_psf, 1.0, "not reducible (occupancy): L = Lo")
    if lo_psf > 100:
        return ReducedLiveLoad(
            lo_psf, lo_psf, 1.0,
            "Lo > 100 psf: not reduced (ASCE 7-22 4.7.3; 20% column allowance not used)",
        )
    kll_at = kll_value * at_sf
    if kll_at < 400:
        return ReducedLiveLoad(
            lo_psf, lo_psf, 1.0, f"KLL*AT = {kll_value:g}*{at_sf:g} = {kll_at:g} < 400: L = Lo"
        )
    raw = 0.25 + 15 / math.sqrt(kll_at)
    limit = 0.5 if floors_supported <= 1 else 0.4
    factor = min(1.0, max(limit, raw))
    l_psf = lo_psf * factor
    note = (
        f"KLL*AT = {kll_value:g}*{at_sf:g} = {kll_at:g}; "
        f"0.25 + 15/sqrt({kll_at:g}) = {raw:.4f}; "
        f"limit {limit}Lo ({floors_supported} floor(s)) -> factor {factor:.4f}; "
        f"L = {lo_psf:g} * {factor:.4f} = {l_psf:.2f} psf"
    )
    return ReducedLiveLoad(lo_psf, l_psf, factor, note)


def roof_live_load(lo_psf: float, at_sf: float) -> ReducedLiveLoad:
    """ASCE 7-22 Eq. 4.8-1 for an ordinary flat roof: Lr = Lo R1 R2, 12 <= Lr <= 20 psf.

    R2 = 1 (flat roof assumed; ASSUMPTIONS Z-4). Roofs with Lo above 20 psf are not
    ordinary roofs and are not reduced.
    """
    if lo_psf > 20:
        return ReducedLiveLoad(lo_psf, lo_psf, 1.0, f"Lo = {lo_psf:g} > 20 psf: not reduced")
    if at_sf <= 200:
        r1 = 1.0
    elif at_sf < 600:
        r1 = 1.2 - 0.001 * at_sf
    else:
        r1 = 0.6
    lr = min(20.0, max(12.0, lo_psf * r1))
    note = (
        f"At = {at_sf:g} sq ft -> R1 = {r1:.3f}; R2 = 1 (flat); "
        f"Lr = {lo_psf:g} * {r1:.3f} = {lo_psf * r1:.2f}, limited to 12..20 -> {lr:.2f} psf"
    )
    return ReducedLiveLoad(lo_psf, lr, lr / lo_psf, note)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _union(zones: list[ResolvedZone]) -> BaseGeometry | None:
    return unary_union([z.geometry for z in zones]) if zones else None


def _most_stringent(criteria) -> str | None:
    present = {c for c in criteria if c}
    if not present:
        return None
    for criterion in _settings()["vibration_precedence"]:
        if criterion in present:
            return criterion
    return min(present)


@lru_cache(maxsize=None)
def _config(name: str) -> dict[str, Any]:
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


def _occupancies() -> dict[str, Any]:
    return _config("occupancies.json")


def _settings() -> dict[str, Any]:
    return _occupancies()["settings"]
