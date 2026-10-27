"""Bones front end -> gravity optimizer: build a ``GravityInput`` from the drawing pad state.

The gravity package stays a black box (``gravity.run``); every translation lives here.
The pad state is ``window.BonesPad.state()`` (the Module 2 / ELF project) sent as JSON:

- ``plan.verts``                outline, ft, origin at the lower-left (already the gravity convention)
- ``levels[j]``                 elevated level j+1: ``h`` = cumulative height (ft); the last one is the roof
- ``plan.lv[j].la``             load-area rectangles on that level: ``{id, x1, y1, x2, y2}``
- ``bones.loads[j+1][id]``      per load-area row: ``{LL, SDL, occupancy}`` (keys may be strings)
- ``bones.grids``               grid lines read from the sketch: ``{x: [{label, pos}], y: [...]}``

Mapping (GRAVITY_OPTIMIZER_SPEC.md decisions log; docs/ASSUMPTIONS.md INT-*):

- Gravity levels: ``L1`` at elevation 0 (slab on grade, story height = h of the first pad
  level), then one level per pad level, named ``L2``, ``L3``, ... with the top one ``ROOF``.
- Each load area with an occupancy becomes one zone per rectangle on its level. Areas with
  no occupancy are left out, so gravity applies its default occupancy (with its warning).
  The roof always uses the ``roof`` occupancy.
- The pad's LL and SDL numbers are not sent: gravity takes LL, partitions, LL reduction and
  the vibration criterion from the occupancy, and SDL from ``config/sdl.json``.
- ``typical_zones_apply_to_all_levels`` is false: the pad copies load areas explicitly.
- ``dimension_lines``: the sketch's grid lines plus the outline's extents, or the outline's
  vertex coordinates when no grid was read.
- ``constraints``, ``objective_weights`` and ``systems`` default to the spec 2.1 example and
  can be overridden per request.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from pydantic import ValidationError

import gravity
from gravity.models import GravityInput

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCCUPANCIES = os.path.join(ROOT, "config", "occupancies.json")

DEFAULT_CONSTRAINTS = {"min_ceiling_height_ft": 9.0, "plenum_allowance_in": 18.0,
                       "max_structure_depth_in": None, "min_bay_ft": 20.0, "max_bay_ft": 45.0}
DEFAULT_WEIGHTS = {"cost": 0.5, "depth": 0.3, "carbon": 0.2}
DEFAULT_SYSTEMS = ["steel"]
_RUN_LOCK = threading.Lock()


def occupancies() -> list[dict[str, Any]]:
    """Occupancy keys for the UI dropdown, from config/occupancies.json."""
    with open(OCCUPANCIES, encoding="utf-8") as f:
        table = json.load(f)["occupancies"]
    return [{"key": k, "ll_psf": v["ll_psf"], "partitions_psf": v["partitions_psf"],
             "ll_reducible": v["ll_reducible"], "is_roof": bool(v.get("is_roof", False))}
            for k, v in table.items()]


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _levels(state: dict) -> tuple[list[dict], list[str]]:
    """Gravity levels (ground first) and the gravity name of each pad level (1-based order)."""
    pad = state.get("levels") or []
    heights = [_num(L.get("h")) for L in pad]
    if not heights or any(h is None for h in heights):
        raise ValueError("Every level needs a height. Check the stories on step 2.")
    if any(b <= a for a, b in zip([0.0] + heights, heights)):
        raise ValueError("Level heights must increase from the ground up.")
    names = [f"L{j + 2}" for j in range(len(pad))]
    names[-1] = "ROOF"
    levels = [{"name": "L1", "elev_ft": 0.0, "floor_to_floor_ft": heights[0]}]
    for j, (name, h) in enumerate(zip(names, heights)):
        top = j == len(heights) - 1
        levels.append({"name": name, "elev_ft": h,
                       "floor_to_floor_ft": None if top else heights[j + 1] - h})
    return levels, names


def _dimension_lines(state: dict, outline: list[list[float]]) -> dict:
    xs0, ys0 = [p[0] for p in outline], [p[1] for p in outline]
    grids = ((state.get("bones") or {}).get("grids") or {})
    gx = [_num(g.get("pos")) for g in grids.get("x") or []]
    gy = [_num(g.get("pos")) for g in grids.get("y") or []]
    gx, gy = [v for v in gx if v is not None], [v for v in gy if v is not None]
    xs = sorted(set(gx + [min(xs0), max(xs0)])) if gx else sorted(set(xs0))
    ys = sorted(set(gy + [min(ys0), max(ys0)])) if gy else sorted(set(ys0))
    return {"x_ft": xs, "y_ft": ys}


def _zones(state: dict, names: list[str], known: set[str]) -> tuple[list[dict], list[str]]:
    lv = (state.get("plan") or {}).get("lv") or []
    loads = (state.get("bones") or {}).get("loads") or {}
    zones, notes, missing = [], [], []
    for j, name in enumerate(names[:-1]):  # the roof uses the roof occupancy
        rows = loads.get(str(j + 1)) or loads.get(j + 1) or {}
        for k, r in enumerate((lv[j] if j < len(lv) else {}).get("la") or []):
            area_id = str(r.get("id"))
            occ = (rows.get(area_id) or {}).get("occupancy")
            if not occ:
                missing.append(f"{name} area {area_id}")
                continue
            if occ not in known:
                notes.append(f"{name} area {area_id}: unknown occupancy {occ!r}; left out")
                continue
            x1, x2 = sorted((float(r["x1"]), float(r["x2"])))
            y1, y2 = sorted((float(r["y1"]), float(r["y2"])))
            zones.append({"id": f"{name}-{area_id}-{k + 1}", "level": name, "occupancy": occ,
                          "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]})
    if missing:
        notes.append("No occupancy chosen for " + ", ".join(dict.fromkeys(missing)) +
                     ": gravity uses its default occupancy there.")
    return zones, notes


def to_gravity_input(state: dict, options: dict | None = None) -> tuple[dict, list[str]]:
    """(gravity input dict, notes about the translation). Raises ValueError on unusable input."""
    options = options or {}
    plan = state.get("plan") or {}
    outline = [[float(p[0]), float(p[1])] for p in plan.get("verts") or []]
    if not plan.get("closed") or len(outline) < 3:
        raise ValueError("Close the floor plan outline on the drawing pad first.")
    levels, names = _levels(state)

    known = {o["key"] for o in occupancies()}
    zones, notes = _zones(state, names, known)

    bones = state.get("bones") or {}
    pf = _num((bones.get("snow") or {}).get("pf_psf"))
    if pf:
        notes.append(f"Roof snow p_f = {pf:g} psf from the hazard panel is not sent: gravity reads "
                     "roof snow from config/occupancies.json (settings.flat_roof_snow_psf).")
    notes.append("LL and SDL typed in the loads table are not sent: gravity takes LL from each "
                 "area's occupancy and SDL from config/sdl.json.")

    constraints = dict(DEFAULT_CONSTRAINTS)
    for key, value in (options.get("constraints") or {}).items():
        if key in constraints:
            constraints[key] = None if value in (None, "") else float(value)
    inp = {
        "project_name": str(state.get("project") or "Bones project"),
        "outline": outline,
        "dimension_lines": _dimension_lines(state, outline),
        "levels": levels,
        "zones": zones,
        "typical_zones_apply_to_all_levels": False,
        "constraints": constraints,
        "objective_weights": options.get("objective_weights") or dict(DEFAULT_WEIGHTS),
        "systems": options.get("systems") or list(DEFAULT_SYSTEMS),
    }
    try:
        GravityInput.model_validate(inp)
    except ValidationError as e:
        raise ValueError("The plan can't be sent to gravity: " + "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())) from e
    return inp, notes


def run(state: dict, options: dict | None = None) -> dict:
    """Translate, run the optimizer, and return its output with the input that was used.

    Runs are serialized: the gravity package shares one SQLite connection across threads.
    """
    inp, notes = to_gravity_input(state, options)
    with _RUN_LOCK:
        output = gravity.run(inp)
    return {"input": inp, "notes": notes, "output": output}
