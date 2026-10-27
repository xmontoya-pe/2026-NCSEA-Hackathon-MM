"""Pydantic v2 models for the gravity optimizer's JSON contract.

This module is the single source of truth for the contract with the integration,
front-end and lateral teams (spec section 4, decisions log section 7).

- Input:  ``GravityInput``  matches ``input.json``  in spec section 2.1.
- Output: ``GravityOutput`` matches ``output.json`` in spec section 3.1.

Field names are copied verbatim from the spec examples. Units follow spec section 1:
feet, kips, psf, ksf, and inches only for member depths. Coordinates are plan-view
feet with the origin at the lower-left of the building bounding box, +X right, +Y up.

Every model forbids unknown keys, so a contract mismatch fails loudly in tests
instead of being silently dropped.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SystemName = Literal["steel", "concrete", "mass_timber"]

# ASCE 7-22 section 2.3.1 gravity combinations (spec 4.5, decisions log section 7).
LoadCombo = Literal[
    "1.4D",
    "1.2D+1.6L+0.5Lr",
    "1.2D+1.6L+0.5S",
    "1.2D+1.6Lr+1.0L",
    "1.2D+1.6S+1.0L",
]

# [x_ft, y_ft] in plan.
Point = tuple[float, float]
# Polygon vertices in feet; closing vertex optional.
Polygon = Annotated[list[Point], Field(min_length=3)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Input (spec section 2.1)
# ---------------------------------------------------------------------------


class DimensionLines(_Model):
    """Dimension lines read off the sketch by the recognizer (feet)."""

    x_ft: list[float]
    y_ft: list[float]


class Level(_Model):
    name: str
    elev_ft: float
    # null for the roof (no story above it).
    floor_to_floor_ft: float | None


class Zone(_Model):
    id: str
    level: str
    # Key into config/occupancies.json (spec 2.2); validated against config, not here.
    occupancy: str
    polygon: Polygon
    no_columns: bool = False


class Constraints(_Model):
    min_ceiling_height_ft: float
    plenum_allowance_in: float
    max_structure_depth_in: float | None = None
    min_bay_ft: float
    max_bay_ft: float

    @model_validator(mode="after")
    def _bay_range(self) -> Constraints:
        if self.min_bay_ft > self.max_bay_ft:
            raise ValueError(
                f"min_bay_ft ({self.min_bay_ft}) exceeds max_bay_ft ({self.max_bay_ft})"
            )
        return self


class ObjectiveWeights(_Model):
    cost: float = Field(ge=0)
    depth: float = Field(ge=0)
    carbon: float = Field(ge=0)


class GravityInput(_Model):
    project_name: str
    outline: Polygon
    dimension_lines: DimensionLines
    levels: list[Level] = Field(min_length=1)
    zones: list[Zone]
    typical_zones_apply_to_all_levels: bool
    constraints: Constraints
    objective_weights: ObjectiveWeights
    systems: list[SystemName] = Field(min_length=1)

    @model_validator(mode="after")
    def _cross_references(self) -> GravityInput:
        level_names = [lvl.name for lvl in self.levels]
        if len(set(level_names)) != len(level_names):
            raise ValueError(f"level names must be unique: {level_names}")

        zone_ids = [z.id for z in self.zones]
        if len(set(zone_ids)) != len(zone_ids):
            raise ValueError(f"zone ids must be unique: {zone_ids}")

        unknown = [z.id for z in self.zones if z.level not in level_names]
        if unknown:
            raise ValueError(f"zones reference undefined levels: {unknown}")
        return self


# ---------------------------------------------------------------------------
# Output (spec section 3.1)
# ---------------------------------------------------------------------------


class Grid(_Model):
    x_ft: list[float]
    y_ft: list[float]


class ColumnStackEntry(_Model):
    """One column segment; ``level`` is the level at the bottom of the story it spans."""

    level: str
    size: str
    Pu_kips: float
    Pd_kips: float
    Pl_kips: float
    Plr_kips: float
    Psnow_kips: float
    governing_combo: LoadCombo


class Column(_Model):
    id: str
    x_ft: float
    y_ft: float
    stack: list[ColumnStackEntry]


class Framing(_Model):
    slab: str
    beam: str
    girder: str
    depth_in: float
    self_wt_psf: float
    # Source row in the bay study DB, for traceability (spec 2.3).
    db_row: int


class Bay(_Model):
    id: str
    level: str
    span_x_ft: float
    span_y_ft: float
    governing_occupancy: str
    ll_psf: float
    sdl_psf: float
    framing: Framing


class LevelResult(_Model):
    level: str
    seismic_weight_kips: float
    center_of_mass_ft: Point
    max_structure_depth_in: float
    clear_height_ft: float


class FoundationReaction(_Model):
    column_id: str
    D_kips: float
    L_kips: float
    Lr_kips: float
    S_kips: float


class Quantities(_Model):
    steel_tons: float
    concrete_cy: float
    timber_cf: float


class Totals(_Model):
    cost_usd: float
    cost_per_sf: float
    carbon_kgco2e: float
    carbon_per_sf: float
    max_depth_in: float


class Scheme(_Model):
    system: SystemName
    # Within-system weighted score, lower is better (spec 4.6).
    rank_score: float
    grid: Grid
    columns: list[Column]
    bays: list[Bay]
    levels: list[LevelResult]
    foundation_reactions: list[FoundationReaction]
    quantities: Quantities
    totals: Totals
    # Runner-up grids, "same shape" as a scheme (spec 3.1). Their own alternates stay empty.
    alternates: list[Scheme] = Field(default_factory=list)
    warnings: list[str]


class GravityOutput(_Model):
    schemes: list[Scheme]


Scheme.model_rebuild()
