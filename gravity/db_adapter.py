"""Read-only adapter around the bay study SQLite database. ALL SQL lives here.

Spec section 2.3 (and section 1: the DB is read-only; one adapter so a schema change
touches one file). Built around the DB as inspected in spec 6 step 2; every assumption
it relies on is listed in ``docs/ASSUMPTIONS.md``.

Public API:

- ``framing_options(system, use, span_x_ft, span_y_ft, ll_psf, sdl_psf, criterion=None)``
- ``available_spans(system, use)``
- ``available_loads(system, use)``

Lookups never interpolate: spans and loads round UP to the next value in the table
(spec 1). An empty list from ``framing_options`` means no DB row fits; the caller marks
the bay infeasible (spec 4.3).

The DB is opened with SQLite's read-only mode. Its path defaults to
``data/bay_studies.sqlite`` and can be overridden with the ``GRAVITY_BAY_DB``
environment variable.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "bay_studies.sqlite"
CONFIG_DIR = REPO_ROOT / "config"

USES = ("floor", "roof")
STRENGTH = "Strength"
LB_PER_TON = 2000.0  # US short ton


@dataclass
class BayFraming:
    """One framing option for a bay, from one DB row (or two, for an envelope)."""

    db_row: int  # bay_designs.bay_id, for traceability
    db_system: str  # e.g. "STL_COMP"
    orientation: str  # "beams_span_x" or "beams_span_y"
    beam_span_ft: float  # DB span_1_ft (rounded-up table value)
    girder_span_ft: float  # DB span_2_ft (rounded-up table value)
    spacing_ft: float
    criterion: str  # DB criterion of the row used
    design_ll_psf: float  # LL the row was designed for
    lookup_ll_psf: float  # LL requested, after the SDL rule (ASSUMPTIONS L-1)
    slab: str
    beam: str  # e.g. "W12X22 @ 10ft"
    girder: str
    depth_in: float
    steel_psf: float  # beams/joists + girders only
    slab_deck_psf: float
    self_wt_psf: float  # steel_psf + slab_deck_psf (ASSUMPTIONS W-1)
    cost_per_sf: float  # from config/costs.json (placeholder rates)
    cost_breakdown: dict[str, float] = field(default_factory=dict)  # USD per sf
    carbon_kgco2e_per_sf: float = 0.0  # DB gwp_kgco2e_per_ft2, excludes columns
    concrete_cy_per_sf: float = 0.0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def framing_options(
    system: str,
    use: str,
    span_x_ft: float,
    span_y_ft: float,
    ll_psf: float,
    sdl_psf: float,
    criterion: str | None = None,
) -> list[BayFraming]:
    """Return every DB framing option that covers this bay, cheapest first.

    ``ll_psf`` is the live load to design for, unreduced, with partitions already added
    (ASSUMPTIONS L-2); for a roof it is max(Lr, flat roof snow) (L-5). ``sdl_psf`` above
    the DB system's design SDL is added to LL for the lookup (L-1). Both beam
    orientations are tried. ``criterion`` names a vibration criterion row set, e.g.
    ``"Walking b=0.03"``; ``None`` uses the Strength rows.
    """
    options: list[BayFraming] = []
    for db_system in _db_systems(system, use):
        design_sdl = _db_system_config(db_system)["design_sdl_psf"]
        excess = max(0.0, sdl_psf - design_sdl)
        lookup_ll = ll_psf + excess
        notes = []
        if excess > 0:
            notes.append(
                f"SDL {sdl_psf:g} psf exceeds the {db_system} design SDL of {design_sdl:g} psf; "
                f"the {excess:g} psf excess is added to LL for the lookup "
                f"(LL {ll_psf:g} -> {lookup_ll:g} psf)"
            )
        if criterion not in (None, STRENGTH) and criterion not in _criteria(db_system):
            raise ValueError(f"criterion {criterion!r} not in bay study for {db_system}")

        spans_1, spans_2 = _spans(db_system)
        orientations = [("beams_span_x", span_x_ft, span_y_ft)]
        if span_x_ft != span_y_ft:
            orientations.append(("beams_span_y", span_y_ft, span_x_ft))
        for orientation, beam_span, girder_span in orientations:
            span_1 = _round_up(beam_span, spans_1)
            span_2 = _round_up(girder_span, spans_2)
            if span_1 is None or span_2 is None:
                continue
            options.extend(
                _options_at(db_system, orientation, span_1, span_2, lookup_ll, criterion, notes)
            )

    return sorted(
        options, key=lambda o: (o.cost_per_sf, o.depth_in, o.db_system, o.db_row, o.orientation)
    )


def available_spans(system: str, use: str) -> tuple[list[float], list[float]]:
    """(beam spans, girder spans) present in the DB for this system and use."""
    spans_1: set[float] = set()
    spans_2: set[float] = set()
    for db_system in _db_systems(system, use):
        s1, s2 = _spans(db_system)
        spans_1.update(s1)
        spans_2.update(s2)
    return sorted(spans_1), sorted(spans_2)


def available_loads(system: str, use: str) -> tuple[list[float], list[float]]:
    """(Strength LL values present, design SDL values) for this system and use."""
    lls: set[float] = set()
    sdls: set[float] = set()
    for db_system in _db_systems(system, use):
        lls.update(_strength_lls(db_system))
        sdls.add(_db_system_config(db_system)["design_sdl_psf"])
    return sorted(lls), sorted(sdls)


# ---------------------------------------------------------------------------
# Lookup logic
# ---------------------------------------------------------------------------


def _options_at(
    db_system: str,
    orientation: str,
    span_1: float,
    span_2: float,
    lookup_ll: float,
    criterion: str | None,
    notes: list[str],
) -> list[BayFraming]:
    strength_ll = _round_up(lookup_ll, _strength_lls(db_system))
    strength_rows = (
        _rows(db_system, span_1, span_2, category=STRENGTH, live_load=strength_ll)
        if strength_ll is not None
        else []
    )

    if criterion is None or criterion == STRENGTH:
        return [
            _to_framing(row, db_system, orientation, lookup_ll, list(notes))
            for row in strength_rows
        ]

    strength_by_spacing = {row["spacing_ft"]: row for row in strength_rows}
    options = []
    for row in _rows(db_system, span_1, span_2, criterion=criterion):
        if row["live_load_psf"] >= lookup_ll:
            # Vibration rows also satisfy strength at their own LL (DB assumptions).
            options.append(_to_framing(row, db_system, orientation, lookup_ll, list(notes)))
            continue
        strength = strength_by_spacing.get(row["spacing_ft"])
        if strength is None:
            continue
        # Decision 2 (ASSUMPTIONS L-6): use the heavier of the two rows and warn.
        heavier = max(
            (strength, row),
            key=lambda r: (r["steel_psf"], r["structural_depth_in"], r is strength),
        )
        note = (
            f"Envelope: no DB row is designed for both '{criterion}' (LL "
            f"{row['live_load_psf']:g} psf) and Strength LL {strength['live_load_psf']:g} psf; "
            f"the heavier row (bay_id {heavier['bay_id']}) is used and is not checked for both"
        )
        options.append(
            _to_framing(heavier, db_system, orientation, lookup_ll, notes + [note])
        )
    return options


def _round_up(value: float, table: list[float]) -> float | None:
    """Smallest table value >= value, or None if value exceeds the table."""
    for candidate in table:  # table is sorted ascending
        if candidate >= value - 1e-9:
            return candidate
    return None


def _to_framing(
    row: sqlite3.Row, db_system: str, orientation: str, lookup_ll: float, warnings: list[str]
) -> BayFraming:
    slab = row["deck_or_slab"]
    spacing = row["spacing_ft"]
    steel_psf = row["steel_psf"]
    slab_deck_psf = _db_system_config(db_system)["slab_deck_psf"]
    deck_key = None
    if slab_deck_psf is None:  # roof systems: deck weight per row
        deck_key, blank_gauge = _roof_deck_key(slab)
        slab_deck_psf = _roof_deck_weight(deck_key)
        if blank_gauge:
            warnings.append(
                f"bay_id {row['bay_id']}: deck '{slab.strip()}' has no gauge; weighed and "
                f"priced as {deck_key} (conservative)"
            )
    breakdown = _cost_breakdown(row, db_system, deck_key, warnings)

    return BayFraming(
        db_row=row["bay_id"],
        db_system=db_system,
        orientation=orientation,
        beam_span_ft=row["span_1_ft"],
        girder_span_ft=row["span_2_ft"],
        spacing_ft=spacing,
        criterion=row["criterion"],
        design_ll_psf=row["live_load_psf"],
        lookup_ll_psf=lookup_ll,
        slab=slab,
        beam=f"{row['member_1']} @ {spacing:g}ft",
        girder=row["member_2"],
        depth_in=row["structural_depth_in"],
        steel_psf=steel_psf,
        slab_deck_psf=slab_deck_psf,
        self_wt_psf=steel_psf + slab_deck_psf,
        cost_per_sf=sum(breakdown.values()),
        cost_breakdown=breakdown,
        carbon_kgco2e_per_sf=row["gwp_kgco2e_per_ft2"],
        concrete_cy_per_sf=row["concrete_cy_per_sf"] or 0.0,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Cost (DB quantities x config/costs.json unit rates; ASSUMPTIONS C-1)
# ---------------------------------------------------------------------------


def _cost_breakdown(
    row: sqlite3.Row, db_system: str, deck_key: str | None, warnings: list[str]
) -> dict[str, float]:
    steel = _costs()["steel"]

    def rate(key: str) -> float:
        return steel[key]["value"]

    steel_psf = row["steel_psf"]
    if db_system == "STL_COMP":
        beam_studs, girder_studs = _composite_studs(row)
        # Per bay (span_1 x span_2): span_2/spacing beams of length span_1, one girder.
        studs_per_sf = beam_studs / (row["span_1_ft"] * row["spacing_ft"]) + girder_studs / (
            row["span_1_ft"] * row["span_2_ft"]
        )
        return {
            "framing_steel": steel_psf / LB_PER_TON * rate("wide_flange_framing_usd_per_ton"),
            "deck": rate("composite_floor_deck_usd_per_sf"),
            "concrete": (row["concrete_cy_per_sf"] or 0.0) * rate("concrete_on_deck_usd_per_cy"),
            "slab_reinforcing": rate("slab_reinforcing_on_deck_usd_per_sf"),
            "studs": studs_per_sf * rate("shear_studs_usd_each"),
        }

    deck = steel["roof_deck_usd_per_sf"][deck_key]["value"]
    if db_system == "STL_ROOF_J":
        joist_plf = _joist_weight_plf(row["member_1"])
        joist_psf = joist_plf / row["spacing_ft"] if joist_plf is not None else None
        if joist_psf is not None and joist_psf <= steel_psf:
            return {
                "joists": joist_psf / LB_PER_TON * rate("open_web_joists_usd_per_ton"),
                "girders": (steel_psf - joist_psf)
                / LB_PER_TON
                * rate("wide_flange_framing_usd_per_ton"),
                "deck": deck,
            }
        warnings.append(
            f"bay_id {row['bay_id']}: joist {row['member_1']!r} not in ref_kcs_joists; "
            "all framing steel priced at the W-shape rate (conservative)"
        )
    return {
        "framing_steel": steel_psf / LB_PER_TON * rate("wide_flange_framing_usd_per_ton"),
        "deck": deck,
    }


_DECK_RE = re.compile(r"^\s*(\S+)\s+(\d*)\s*ga\b")


def _roof_deck_key(deck_or_slab: str) -> tuple[str, bool]:
    """'1.5B 20 ga roof deck' -> ('1.5B 20 ga', False); blank gauge -> 18 ga, True."""
    match = _DECK_RE.match(deck_or_slab)
    if match is None:
        raise ValueError(f"unrecognized roof deck description {deck_or_slab!r}")
    deck, gauge = match.groups()
    if not gauge:
        return f"{deck} 18 ga", True  # ASSUMPTIONS C-2
    return f"{deck} {gauge} ga", False


# ---------------------------------------------------------------------------
# SQL (read-only)
# ---------------------------------------------------------------------------

_ROW_COLUMNS = (
    "bay_id, system_id, source_table, source_design_id, span_1_ft, span_2_ft, spacing_ft, "
    "criterion_category, criterion, live_load_psf, deck_or_slab, member_1, member_2, "
    "structural_depth_in, steel_psf, concrete_cy_per_sf, gwp_kgco2e_per_ft2"
)


def db_path() -> Path:
    return Path(os.environ.get("GRAVITY_BAY_DB", DEFAULT_DB_PATH))


def _connect() -> sqlite3.Connection:
    return _connect_to(str(db_path().resolve()))


@lru_cache(maxsize=None)
def _connect_to(path: str) -> sqlite3.Connection:
    if not Path(path).is_file():
        raise FileNotFoundError(
            f"bay study database not found at {path}; set GRAVITY_BAY_DB to its location"
        )
    con = sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _rows(
    db_system: str,
    span_1: float,
    span_2: float,
    *,
    category: str | None = None,
    criterion: str | None = None,
    live_load: float | None = None,
) -> list[sqlite3.Row]:
    sql = (
        f"SELECT {_ROW_COLUMNS} FROM bay_designs "
        "WHERE system_id = ? AND span_1_ft = ? AND span_2_ft = ? AND solved = 1"
    )
    params: list[Any] = [db_system, span_1, span_2]
    if category is not None:
        sql += " AND criterion_category = ?"
        params.append(category)
    if criterion is not None:
        sql += " AND criterion = ?"
        params.append(criterion)
    if live_load is not None:
        sql += " AND live_load_psf = ?"
        params.append(live_load)
    return _connect().execute(sql + " ORDER BY bay_id", params).fetchall()


@lru_cache(maxsize=None)
def _spans(db_system: str) -> tuple[list[float], list[float]]:
    con = _connect()
    s1 = [r[0] for r in con.execute(
        "SELECT DISTINCT span_1_ft FROM bay_designs WHERE system_id = ? ORDER BY 1", (db_system,)
    )]
    s2 = [r[0] for r in con.execute(
        "SELECT DISTINCT span_2_ft FROM bay_designs WHERE system_id = ? ORDER BY 1", (db_system,)
    )]
    return s1, s2


@lru_cache(maxsize=None)
def _strength_lls(db_system: str) -> list[float]:
    return [r[0] for r in _connect().execute(
        "SELECT DISTINCT live_load_psf FROM bay_designs "
        "WHERE system_id = ? AND criterion_category = ? ORDER BY 1",
        (db_system, STRENGTH),
    )]


@lru_cache(maxsize=None)
def _criteria(db_system: str) -> frozenset[str]:
    return frozenset(r[0] for r in _connect().execute(
        "SELECT DISTINCT criterion FROM bay_designs WHERE system_id = ?", (db_system,)
    ))


def _composite_studs(row: sqlite3.Row) -> tuple[float, float]:
    """(studs per beam, studs per girder) from des_steel_composite."""
    if row["source_table"] != "des_steel_composite":
        raise ValueError(f"bay_id {row['bay_id']}: unexpected source {row['source_table']!r}")
    found = _connect().execute(
        "SELECT beam_studs, girder_studs FROM des_steel_composite WHERE design_id = ?",
        (row["source_design_id"],),
    ).fetchone()
    if found is None:
        raise ValueError(f"bay_id {row['bay_id']}: design {row['source_design_id']} not found")
    return found["beam_studs"] or 0.0, found["girder_studs"] or 0.0


@lru_cache(maxsize=None)
def _joist_weight_plf(joist: str) -> float | None:
    found = _connect().execute(
        "SELECT weight_plf FROM ref_kcs_joists WHERE joist = ?", (joist,)
    ).fetchone()
    return None if found is None else found[0]


@lru_cache(maxsize=None)
def _roof_deck_weight(deck_key: str) -> float:
    deck, gauge, _ = deck_key.split()
    found = _connect().execute(
        "SELECT weight_psf FROM ref_roof_deck_spans WHERE deck = ? AND gage = ?",
        (deck, int(gauge)),
    ).fetchone()
    if found is None:
        raise ValueError(f"no deck weight for {deck_key!r} in ref_roof_deck_spans")
    return found[0]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _config(name: str) -> dict[str, Any]:
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


def _costs() -> dict[str, Any]:
    return _config("costs.json")


def _db_systems(system: str, use: str) -> list[str]:
    if use not in USES:
        raise ValueError(f"use must be one of {USES}, got {use!r}")
    try:
        return _config("systems.json")["systems"][system][use]["db_systems"]
    except KeyError:
        raise ValueError(f"no bay study mapping for system {system!r} ({use})") from None


def _db_system_config(db_system: str) -> dict[str, Any]:
    return _config("systems.json")["db_systems"][db_system]
