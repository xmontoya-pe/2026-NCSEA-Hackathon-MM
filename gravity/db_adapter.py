"""Read-only adapter around the bay study SQLite database. ALL SQL lives here.

Spec section 2.3 (and section 1: the DB is read-only; one adapter so a schema change
touches one file). Built around the DB as inspected in spec 6 step 2; every assumption
it relies on is listed in ``docs/ASSUMPTIONS.md``.

Public API:

- ``framing_options(system, use, span_x_ft, span_y_ft, ll_psf, sdl_psf, criterion=None)``
- ``available_spans(system, use)``
- ``available_loads(system, use)``
- ``family(system)``, ``variants(family)``, ``label(system)`` (config/systems.json)

A ``system`` is one uniform structural system (``steel``, ``concrete_pt_fp``,
``mass_timber``, ...); its ``family`` is the output system it competes in. Concrete DB
systems hold floor and roof rows under one ``system_id``, told apart by the ``use``
column (``shared_floor_roof`` in config).

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
    spacing_ft: float | None  # None for flat plates and pan joists
    criterion: str  # DB criterion of the row used
    design_ll_psf: float  # LL the row was designed for
    lookup_ll_psf: float  # LL requested, after the SDL rule (ASSUMPTIONS L-1)
    slab: str
    beam: str  # e.g. "W12X22 @ 10ft"; concrete and timber members as named in the DB
    girder: str
    depth_in: float
    steel_psf: float  # steel: beams/joists + girders; concrete: rebar + PT; timber: 0
    slab_deck_psf: float  # steel deck/slab, concrete equivalent thickness, CLT (+ topping)
    self_wt_psf: float  # member weight + slab_deck_psf (ASSUMPTIONS W-1, W-3)
    cost_per_sf: float  # from config/costs.json (placeholder rates)
    cost_breakdown: dict[str, float] = field(default_factory=dict)  # USD per sf
    carbon_kgco2e_per_sf: float = 0.0  # DB gwp_kgco2e_per_ft2, excludes columns
    concrete_cy_per_sf: float = 0.0
    timber_cf_per_sf: float = 0.0  # glulam + CLT, cubic feet per sq ft of floor
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
        use_filter = _use_filter(db_system, use)
        design_sdl = _design_sdl(db_system, use)
        excess = max(0.0, sdl_psf - design_sdl)
        lookup_ll = ll_psf + excess
        notes = []
        if excess > 0:
            notes.append(
                f"SDL {sdl_psf:g} psf exceeds the {db_system} design SDL of {design_sdl:g} psf; "
                f"the {excess:g} psf excess is added to LL for the lookup "
                f"(LL {ll_psf:g} -> {lookup_ll:g} psf)"
            )
        db_criterion, note = _map_criterion(db_system, use_filter, criterion)
        if note:
            notes.append(note)

        spans_1, spans_2 = _spans(db_system, use_filter)
        orientations = [("beams_span_x", span_x_ft, span_y_ft)]
        if span_x_ft != span_y_ft:
            orientations.append(("beams_span_y", span_y_ft, span_x_ft))
        for orientation, beam_span, girder_span in orientations:
            span_1 = _round_up(beam_span, spans_1)
            span_2 = _round_up(girder_span, spans_2)
            if span_1 is None or span_2 is None:
                continue
            options.extend(_options_at(db_system, use_filter, orientation, span_1, span_2,
                                       lookup_ll, db_criterion, notes))

    return sorted(
        options, key=lambda o: (o.cost_per_sf, o.depth_in, o.db_system, o.db_row, o.orientation)
    )


def available_spans(system: str, use: str) -> tuple[list[float], list[float]]:
    """(beam spans, girder spans) present in the DB for this system and use."""
    spans_1: set[float] = set()
    spans_2: set[float] = set()
    for db_system in _db_systems(system, use):
        s1, s2 = _spans(db_system, _use_filter(db_system, use))
        spans_1.update(s1)
        spans_2.update(s2)
    return sorted(spans_1), sorted(spans_2)


def available_loads(system: str, use: str) -> tuple[list[float], list[float]]:
    """(Strength LL values present, design SDL values) for this system and use."""
    lls: set[float] = set()
    sdls: set[float] = set()
    for db_system in _db_systems(system, use):
        lls.update(_strength_lls(db_system, _use_filter(db_system, use)))
        sdls.add(_design_sdl(db_system, use))
    return sorted(lls), sorted(sdls)


def family(system: str) -> str:
    """Output system (steel, concrete, mass_timber) a structural system competes in."""
    try:
        return _config("systems.json")["systems"][system].get("family", system)
    except KeyError:
        raise ValueError(f"unknown system {system!r} (config/systems.json)") from None


def variants(family_name: str) -> list[str]:
    """Structural systems of one family, in config order (e.g. the four concrete systems)."""
    systems = _config("systems.json")["systems"]
    return [name for name, entry in systems.items() if entry.get("family", name) == family_name]


def label(system: str) -> str:
    return _config("systems.json")["systems"][system].get("label", system)


def material(db_system: str) -> str:
    return _db_system_config(db_system).get("material", "steel")


# ---------------------------------------------------------------------------
# Lookup logic
# ---------------------------------------------------------------------------


def _map_criterion(
    db_system: str, use_filter: str | None, criterion: str | None
) -> tuple[str | None, str | None]:
    """The DB's name for a vibration criterion (config ``criteria``), and a note when the
    DB system has none: its Strength rows are used instead (ASSUMPTIONS V-1)."""
    if criterion in (None, STRENGTH):
        return criterion, None
    mapping = _db_system_config(db_system).get("criteria")
    if mapping is None:  # names as stored in the DB (steel)
        if criterion not in _criteria(db_system, use_filter):
            raise ValueError(f"criterion {criterion!r} not in bay study for {db_system}")
        return criterion, None
    mapped = mapping.get(criterion)
    if mapped is None or mapped not in _criteria(db_system, use_filter):
        return None, (f"{db_system} has no '{criterion}' rows; Strength rows used "
                      "(floor vibration not checked)")
    return mapped, None


def _options_at(
    db_system: str,
    use_filter: str | None,
    orientation: str,
    span_1: float,
    span_2: float,
    lookup_ll: float,
    criterion: str | None,
    notes: list[str],
) -> list[BayFraming]:
    strength_ll = _round_up(lookup_ll, _strength_lls(db_system, use_filter))
    strength_rows = (
        _rows(db_system, span_1, span_2, use_filter=use_filter, category=STRENGTH,
              live_load=strength_ll)
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
    for row in _rows(db_system, span_1, span_2, use_filter=use_filter, criterion=criterion):
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
            key=lambda r: (_member_psf(r, db_system), r["structural_depth_in"], r is strength),
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


def _member_psf(row: sqlite3.Row, db_system: str) -> float:
    """Weight of the framing members alone (psf): steel framing, glulam; 0 for concrete,
    whose members are in its equivalent thickness."""
    cfg = _db_system_config(db_system)
    kind = cfg.get("material", "steel")
    if kind == "concrete":
        return (row["concrete_equiv_thickness_in"] or 0.0) / 12.0 * cfg["concrete_pcf"]
    if kind == "mass_timber":
        return (row["timber_bf_per_sf"] or 0.0) * cfg["glulam_pcf"] / 12.0
    return row["steel_psf"] or 0.0


def _to_framing(
    row: sqlite3.Row, db_system: str, orientation: str, lookup_ll: float, warnings: list[str]
) -> BayFraming:
    cfg = _db_system_config(db_system)
    kind = cfg.get("material", "steel")
    slab = row["deck_or_slab"]
    spacing = row["spacing_ft"]
    deck_key = None
    timber_cf = 0.0
    if kind == "concrete":  # ASSUMPTIONS W-3: equivalent thickness x unit weight
        slab_deck_psf = _member_psf(row, db_system)
        member_psf = 0.0
        steel_psf = (row["rebar_psf"] or 0.0) + (row["pt_psf"] or 0.0)
    elif kind == "mass_timber":  # W-4: CLT (+ topping) from config, glulam from bf
        slab_deck_psf = cfg["slab_deck_psf"]
        member_psf = _member_psf(row, db_system)
        steel_psf = 0.0
        timber_cf = ((row["timber_bf_per_sf"] or 0.0) + (row["clt_thickness_in"] or 0.0)) / 12.0
    else:
        steel_psf = row["steel_psf"]
        member_psf = steel_psf
        slab_deck_psf = cfg["slab_deck_psf"]
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
        beam=f"{row['member_1']} @ {spacing:g}ft" if spacing else str(row["member_1"]),
        girder=str(row["member_2"] or ""),
        depth_in=row["structural_depth_in"],
        steel_psf=steel_psf,
        slab_deck_psf=slab_deck_psf,
        self_wt_psf=member_psf + slab_deck_psf,
        cost_per_sf=sum(breakdown.values()),
        cost_breakdown=breakdown,
        carbon_kgco2e_per_sf=row["gwp_kgco2e_per_ft2"] or 0.0,
        concrete_cy_per_sf=row["concrete_cy_per_sf"] or 0.0,
        timber_cf_per_sf=timber_cf,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Cost (DB quantities x config/costs.json unit rates; ASSUMPTIONS C-1)
# ---------------------------------------------------------------------------


def _cost_breakdown(
    row: sqlite3.Row, db_system: str, deck_key: str | None, warnings: list[str]
) -> dict[str, float]:
    kind = _db_system_config(db_system).get("material", "steel")
    if kind == "concrete":
        return _concrete_cost(row, db_system)
    if kind == "mass_timber":
        return _timber_cost(row)
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


def _concrete_cost(row: sqlite3.Row, db_system: str) -> dict[str, float]:
    """Concrete, formwork, rebar, PT and stud rails per sq ft (ASSUMPTIONS C-6)."""
    rates = _costs()["concrete"]

    def rate(key: str) -> float:
        return rates[key]["value"]

    out = {
        "concrete": (row["concrete_cy_per_sf"] or 0.0) * rate("structural_concrete_usd_per_cy"),
        "formwork": rate(_db_system_config(db_system)["formwork_cost_key"]),
        "rebar": (row["rebar_psf"] or 0.0) / LB_PER_TON * rate("rebar_usd_per_ton"),
    }
    if row["pt_psf"]:
        out["pt"] = row["pt_psf"] * rate("pt_strand_usd_per_lb")
    if row["shear_studs"] == "Yes":
        out["stud_rails"] = rate("stud_rails_usd_per_sf")
    return out


def _timber_cost(row: sqlite3.Row) -> dict[str, float]:
    """Glulam, CLT and topping per sq ft (ASSUMPTIONS C-7)."""
    rates = _costs()["mass_timber"]

    def rate(key: str) -> float:
        return rates[key]["value"]

    out = {
        "glulam": (row["timber_bf_per_sf"] or 0.0) * rate("glulam_framing_usd_per_bf"),
        "clt": (row["clt_thickness_in"] or 0.0) / 12.0 * rate("clt_panels_usd_per_cf"),
    }
    if (row["concrete_cy_per_sf"] or 0.0) > 0:
        out["topping"] = rate("concrete_topping_usd_per_sf")
    return out


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
    "bay_id, system_id, source_table, source_design_id, use, span_1_ft, span_2_ft, "
    "spacing_ft, criterion_category, criterion, live_load_psf, deck_or_slab, member_1, "
    "member_2, structural_depth_in, steel_psf, rebar_psf, pt_psf, "
    "concrete_equiv_thickness_in, timber_bf_per_sf, clt_thickness_in, shear_studs, "
    "concrete_cy_per_sf, gwp_kgco2e_per_ft2"
)
_USE_NAMES = {"floor": "Floor", "roof": "Roof"}  # bay_designs.use values


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
    # One cached read-only connection. Web servers call run() from worker threads, so it may
    # be used from a thread other than the one that opened it (callers serialize their runs).
    con = sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _use_clause(use_filter: str | None) -> tuple[str, tuple]:
    return ("", ()) if use_filter is None else (" AND use = ?", (use_filter,))


def _rows(
    db_system: str,
    span_1: float,
    span_2: float,
    *,
    use_filter: str | None = None,
    category: str | None = None,
    criterion: str | None = None,
    live_load: float | None = None,
) -> list[sqlite3.Row]:
    sql = (
        f"SELECT {_ROW_COLUMNS} FROM bay_designs "
        "WHERE system_id = ? AND span_1_ft = ? AND span_2_ft = ? AND solved = 1"
    )
    params: list[Any] = [db_system, span_1, span_2]
    if use_filter is not None:
        sql += " AND use = ?"
        params.append(use_filter)
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
def _spans(db_system: str, use_filter: str | None = None) -> tuple[list[float], list[float]]:
    con = _connect()
    clause, extra = _use_clause(use_filter)
    s1 = [r[0] for r in con.execute(
        f"SELECT DISTINCT span_1_ft FROM bay_designs WHERE system_id = ?{clause} ORDER BY 1",
        (db_system, *extra),
    )]
    s2 = [r[0] for r in con.execute(
        f"SELECT DISTINCT span_2_ft FROM bay_designs WHERE system_id = ?{clause} ORDER BY 1",
        (db_system, *extra),
    )]
    return s1, s2


@lru_cache(maxsize=None)
def _strength_lls(db_system: str, use_filter: str | None = None) -> list[float]:
    clause, extra = _use_clause(use_filter)
    return [r[0] for r in _connect().execute(
        "SELECT DISTINCT live_load_psf FROM bay_designs "
        f"WHERE system_id = ? AND criterion_category = ?{clause} ORDER BY 1",
        (db_system, STRENGTH, *extra),
    )]


@lru_cache(maxsize=None)
def _criteria(db_system: str, use_filter: str | None = None) -> frozenset[str]:
    clause, extra = _use_clause(use_filter)
    return frozenset(r[0] for r in _connect().execute(
        f"SELECT DISTINCT criterion FROM bay_designs WHERE system_id = ?{clause}",
        (db_system, *extra),
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


def carbon_factor(material: str) -> tuple[float, str]:
    """(A1-A3 factor, unit) from the DB carbon_factors table, e.g. (1220, 'kg CO2e/t')."""
    found = _connect().execute(
        "SELECT gwp_a1a3, unit FROM carbon_factors WHERE material = ?", (material,)
    ).fetchone()
    if found is None:
        raise ValueError(f"no carbon factor for {material!r} in carbon_factors")
    return float(found[0]), found[1]


def review_dump(exclude_prefix: str = "STL") -> str:
    """Plain-text dump of the DB tables and rows needed to map a new system (spec 6,
    step 2 / prompt 8), for review by the team. Read-only."""
    con = _connect()
    queries = [
        ("systems", "SELECT * FROM systems", ()),
        ("db_info", "SELECT * FROM db_info", ()),
        ("carbon_factors", "SELECT material, gwp_a1a3, unit FROM carbon_factors", ()),
        ("assumptions", "SELECT * FROM assumptions WHERE system_id NOT LIKE ?",
         (f"{exclude_prefix}%",)),
        ("criteria per system",
         "SELECT system_id, use, source_table, criterion_category, criterion, live_load_psf, "
         "COUNT(*) AS n, SUM(solved) AS solved FROM bay_designs WHERE system_id NOT LIKE ? "
         "GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 2, 4, 5", (f"{exclude_prefix}%",)),
        ("spans per system",
         "SELECT system_id, use, GROUP_CONCAT(DISTINCT span_1_ft), GROUP_CONCAT(DISTINCT "
         "span_2_ft), GROUP_CONCAT(DISTINCT spacing_ft) FROM bay_designs "
         "WHERE system_id NOT LIKE ? GROUP BY 1, 2", (f"{exclude_prefix}%",)),
    ]
    systems = [r[0] for r in con.execute(
        "SELECT DISTINCT system_id FROM bay_designs WHERE system_id NOT LIKE ? ORDER BY 1",
        (f"{exclude_prefix}%",))]
    for s in systems:
        queries.append((f"sample solved rows {s}",
                        "SELECT * FROM bay_designs WHERE system_id = ? AND solved = 1 "
                        "ORDER BY ABS(span_1_ft - 30) + ABS(span_2_ft - 30), bay_id LIMIT 4",
                        (s,)))
    queries += [
        ("flat plate: columns and load cases",
         "SELECT system, load_case, sdl_psf, ll_psf, stud_rails, GROUP_CONCAT(DISTINCT "
         "column_in), COUNT(*) FROM des_concrete_flat_plate GROUP BY 1, 2, 3, 4, 5", ()),
        ("flat plate sample 30x30",
         "SELECT * FROM des_concrete_flat_plate WHERE l1_ft = 30 AND l2_ft = 30", ()),
        ("mass timber criteria",
         "SELECT use, criterion, ll_psf, cd, solution, COUNT(*) FROM des_mass_timber "
         "GROUP BY 1, 2, 3, 4, 5", ()),
        ("ref_clt_spans", "SELECT * FROM ref_clt_spans", ()),
    ]
    lines = []
    for title, sql, params in queries:
        lines += ["=" * 80, title]
        try:
            cur = con.execute(sql, params)
        except sqlite3.Error as exc:
            lines.append(f"(query failed: {exc})")
            continue
        lines.append(str([d[0] for d in cur.description]))
        lines += [str(tuple(row)) for row in cur.fetchall()]
    return "\n".join(lines) + "\n"


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


def _use_filter(db_system: str, use: str) -> str | None:
    """'Floor' / 'Roof' for DB systems that hold both under one system_id, else None."""
    return _USE_NAMES[use] if _db_system_config(db_system).get("shared_floor_roof") else None


def _design_sdl(db_system: str, use: str) -> float:
    value = _db_system_config(db_system)["design_sdl_psf"]
    return float(value[use] if isinstance(value, dict) else value)
