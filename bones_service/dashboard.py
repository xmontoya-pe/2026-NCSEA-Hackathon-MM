"""Step 5, client dashboard: gravity and lateral results for each scheme, scored by priorities.

``build(state, options)`` turns the Bones drawing-pad state (``window.BonesPad.state()``, the
same dict ``gravity_bridge`` reads) into what ``static/dashboard.html`` draws:

1. Gravity: ``gravity_bridge.run`` with all three systems (concrete, steel, mass timber).
2. Lateral: for each scheme, the pad state goes to ``elf_engine.analyze`` with that scheme's
   lateral system from ``config/dashboard.json`` and the scheme's own level seismic weights
   from gravity, so each base shear reflects that system's weight (docs/ASSUMPTIONS.md DSH-2).
3. Metrics per scheme (DSH-3 to DSH-7):
   - structure cost: gravity framing and columns plus a lateral allowance that scales with
     seismic demand (``config/costs.json`` ``lateral``), with a range (``cost_range_pct``)
   - time to top out (``config/schedule.json``)
   - embodied carbon (gravity framing and columns)
   - required floor-to-floor (minimum ceiling + plenum + deepest floor framing)
   - base shear (lateral)
4. ``fit_scores``: the four slider weights applied to cost, schedule, carbon and base shear,
   each min-max normalized across the schemes (DSH-8). The page repeats this formula live.

Every rate is a placeholder until an estimator and the team check it. Problems with one
scheme come back as that scheme's ``conditions`` or the top-level ``warnings``; only an
unusable pad state raises ``ValueError``.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import sys
from typing import Any

from shapely.geometry import Polygon

from bones_service import gravity_bridge

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import elf_engine  # noqa: E402  (module at the repo root)

CONFIG = os.path.join(ROOT, "config")
FACTORS = ("cost", "schedule", "carbon", "seismic")
LOWER_IS_BETTER = {  # option metric behind each slider factor
    "cost": "cost_usd", "schedule": "weeks", "carbon": "carbon_t", "seismic": "base_shear_kips",
}
BEST_WORST = {  # metric: (text when lowest of the three, text when highest)
    "cost_usd": ("Lowest expected structure cost", "Highest expected structure cost"),
    "weeks": ("Fastest to top out", "Slowest to top out"),
    "carbon_t": ("Lowest embodied carbon", "Highest embodied carbon"),
    "base_shear_kips": ("Smallest seismic forces, so a lighter lateral system and foundations",
                        "Largest seismic forces, so a heavier lateral system and foundations"),
    "f2f_ft": ("Thinnest floors, so the lowest building height",
               "Deepest floors, so the tallest building for the same ceilings"),
}


def _config(name: str) -> dict[str, Any]:
    with open(os.path.join(CONFIG, name), encoding="utf-8") as f:
        return json.load(f)


def _num(v) -> float | None:
    return gravity_bridge._num(v)


# ---------------------------------------------------------------------------
# Scoring (DSH-8)
# ---------------------------------------------------------------------------


def normalized(metrics: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Per option and factor: 1 for the lowest value, 0 for the highest, linear between.
    All equal: 1. A missing value scores 0 (treated as the worst)."""
    out: list[dict[str, float]] = [{} for _ in metrics]
    for factor, key in LOWER_IS_BETTER.items():
        vals = [m.get(key) for m in metrics]
        known = [v for v in vals if v is not None and math.isfinite(v)]
        lo, hi = (min(known), max(known)) if known else (0.0, 0.0)
        for i, v in enumerate(vals):
            if v is None or not math.isfinite(v):
                out[i][factor] = 0.0
            else:
                out[i][factor] = (hi - v) / (hi - lo) if hi > lo else 1.0
    return out


def fit_scores(metrics: list[dict[str, Any]], weights: dict[str, float]) -> list[int]:
    """Fit score 0..100 per option: sum(weight x normalized) / sum(weights). All weights 0: 0."""
    wsum = sum(float(weights.get(k, 0)) for k in FACTORS)
    if wsum <= 0:
        return [0] * len(metrics)
    norms = normalized(metrics)
    return [round(100 * sum(float(weights.get(k, 0)) * n[k] for k in FACTORS) / wsum) for n in norms]


# ---------------------------------------------------------------------------
# Schedule (DSH-5)
# ---------------------------------------------------------------------------


def schedule_weeks(system: str, weight_psf: float, plate_sf: float, floors: int,
                   cfg: dict[str, Any] | None = None) -> dict[str, float]:
    """Weeks to top out = foundations + any wait for the structure beyond them + floors x cycle.

    foundations = (base + per_100_psf x weight / 100) x sqrt(plate / reference plate)
    cycle       = floor cycle at the reference plate x (plate / reference plate) ^ exponent
    """
    cfg = cfg or _config("schedule.json")
    scale = plate_sf / cfg["reference_plate_sf"]
    f = cfg["foundations"]
    fdn = (f["base_weeks"] + f["weeks_per_100_psf"] * weight_psf / 100.0) * math.sqrt(scale)
    s = cfg["systems"][system]
    wait = max(0.0, s["lead_time_weeks"] - fdn)
    cycle = s["floor_cycle_weeks"] * scale ** cfg["cycle_scale_exponent"]
    sup = floors * cycle
    return {"foundations": fdn, "procurement_wait": wait, "floor_cycle": cycle,
            "superstructure": sup, "total": fdn + wait + sup}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


def build(state: dict, options: dict | None = None) -> dict[str, Any]:
    """Dashboard data for one pad state. Raises ValueError if the plan can't be analyzed."""
    cfg = _config("dashboard.json")
    costs = _config("costs.json")
    sched = _config("schedule.json")
    keys = list(cfg["systems"])  # display order
    grav = gravity_bridge.run(state, {**(options or {}), "systems": keys})
    inp, out = grav["input"], grav["output"]
    schemes = {s["system"]: s for s in out["schemes"]}
    warnings = list(grav["notes"])

    area = Polygon(inp["outline"]).area
    stories = len(state.get("levels") or [])
    gross_sf = area * stories
    hazard, notes = _hazard(state, cfg)
    warnings += notes
    ctx = {"inp": inp, "state": state, "area": area, "stories": stories, "gross_sf": gross_sf,
           "hazard": hazard, "costs": costs, "sched": sched}

    opts = []
    for key in keys:
        if key not in schemes:
            warnings.append(f"No {cfg['systems'][key]['name'].lower()} scheme came back from the "
                            "gravity optimizer, so it is left off the dashboard")
            continue
        opts.append(_option(key, cfg["systems"][key], schemes[key], ctx))
    _pros_cons(opts, cfg)

    weights = {k: cfg["default_weights"][k] for k in FACTORS}
    for o, score in zip(opts, fit_scores([o["metrics"] for o in opts], weights)):
        o["default_score"] = score
    return {
        "project": {"name": inp["project_name"], "stories": stories, "gross_area_sf": gross_sf,
                    "plate_sf": area, "height_ft": _roof_height(state), "sdc": hazard["sdc"],
                    "prepared_by": cfg.get("prepared_by") or "", "hazard": hazard},
        "weights": weights,
        "options": opts,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _hazard(state: dict, cfg: dict) -> tuple[dict[str, Any], list[str]]:
    """sds, sd1, Ie and Risk Category from the pad's hazard step, else placeholders (DSH-1)."""
    d = cfg["default_hazard"]
    query = (((state.get("bones") or {}).get("hazard") or {}).get("query") or {})
    rc = query.get("risk_category") or state.get("rc")
    if rc not in elf_engine.IE_BY_RISK:
        rc = d["risk_category"]
    hz = {"sds": _num(state.get("sds")), "sd1": _num(state.get("sd1")), "ie": _num(state.get("ie"))}
    missing = [k for k in ("sds", "sd1") if hz[k] is None]
    for k in missing:
        hz[k] = d[k]
    if hz["ie"] is None:
        hz["ie"] = elf_engine.IE_BY_RISK[rc]
    hz["risk_category"] = rc
    hz["sdc"] = elf_engine.sdc_of(hz["sds"], hz["sd1"], rc)
    hz["source"] = "placeholder" if missing else "site hazard lookup"
    notes = []
    if missing:
        notes.append("Site hazard not set, so the lateral analysis uses placeholder SDS = "
                     f"{d['sds']:g} and SD1 = {d['sd1']:g}. Run the site hazard lookup in Bones "
                     "for this site's values.")
    return hz, notes


def _roof_height(state: dict) -> float | None:
    levels = state.get("levels") or []
    return _num(levels[-1].get("h")) if levels else None


def _option(key: str, scfg: dict, scheme: dict, ctx: dict) -> dict[str, Any]:
    inp, costs = ctx["inp"], ctx["costs"]
    gross_sf, stories = ctx["gross_sf"], ctx["stories"]
    lat = _lateral(scfg, scheme, ctx)
    totals = scheme["totals"]

    # Cost (DSH-3, DSH-4)
    lat_cfg = costs["lateral"]
    rate = lat_cfg[scfg["lateral_cost_key"]]["value"]
    height_factor = 1 + lat_cfg["height_factor_per_story"]["value"] * max(0, stories - 1)
    v = lat.get("V_kips")
    demand_psf = v * 1000.0 / gross_sf if v and gross_sf else None
    lateral_usd = rate * demand_psf * height_factor * gross_sf if demand_psf is not None else 0.0
    gravity_usd = float(totals["cost_usd"])
    mid = gravity_usd + lateral_usd
    pct = costs["cost_range_pct"][key]["value"] / 100.0

    # Schedule (DSH-5): seismic weight per sq ft of framed floor from gravity
    w_kips = sum(lv["seismic_weight_kips"] for lv in scheme["levels"])
    weight_psf = w_kips * 1000.0 / gross_sf if gross_sf else 0.0
    sched = schedule_weeks(key, weight_psf, ctx["area"], stories, ctx["sched"])

    # Floor-to-floor (DSH-6): deepest floor framing (roof only if it is the only level)
    c = inp["constraints"]
    floors = [lv for lv in scheme["levels"] if lv["level"] != "ROOF"] or scheme["levels"]
    depth_in = max((lv["max_structure_depth_in"] for lv in floors), default=0.0)
    f2f = c["min_ceiling_height_ft"] + c["plenum_allowance_in"] / 12.0 + depth_in / 12.0

    gravity_label = scfg["gravity"]
    if scfg.get("gravity_from_scheme"):  # concrete: the system the optimizer picked
        m = next((re.match(r"^Concrete system: (.+?) \(", w) for w in scheme["warnings"]
                  if w.startswith("Concrete system: ")), None)
        gravity_label = m.group(1) if m else gravity_label

    return {
        "key": key,
        "name": scfg["name"],
        "system": f"{gravity_label} with {scfg['lateral']}",
        "figure": scfg["figure"],
        "metrics": {
            "cost_usd": mid, "cost_lo_usd": mid * (1 - pct), "cost_hi_usd": mid * (1 + pct),
            "cost_lo_per_sf": mid * (1 - pct) / gross_sf if gross_sf else None,
            "cost_hi_per_sf": mid * (1 + pct) / gross_sf if gross_sf else None,
            "weeks": sched["total"], "carbon_t": float(totals["carbon_kgco2e"]) / 1000.0,
            "f2f_ft": f2f, "base_shear_kips": v,
        },
        "basis": {
            "gravity_cost_usd": gravity_usd, "lateral_cost_usd": lateral_usd,
            "lateral_rate_usd_per_sf_per_psf": rate, "height_factor": height_factor,
            "seismic_demand_psf": demand_psf, "cost_range_pct": pct * 100,
            "seismic_weight_kips": w_kips, "seismic_weight_psf": weight_psf,
            "schedule_weeks": sched, "max_floor_depth_in": depth_in,
            "quantities": scheme["quantities"],
        },
        "lateral": lat,
        "conditions": _conditions(scheme, lat, scfg, ctx),
        "pros": [], "cons": [],
    }


def _lateral(scfg: dict, scheme: dict, ctx: dict) -> dict[str, Any]:
    """ELF for one scheme with its own seismic weights (DSH-2)."""
    state, inp, area, hz = ctx["state"], ctx["inp"], ctx["area"], ctx["hazard"]
    project = copy.deepcopy(state)
    names = [lv["name"] for lv in inp["levels"]][1:]  # pad level j is gravity level j + 1
    weight = {lv["level"]: lv["seismic_weight_kips"] for lv in scheme["levels"]}
    for name, level in zip(names, project.get("levels") or []):
        if name in weight and area > 0:
            level["psf"] = weight[name] * 1000.0 / area
            level["la"] = False
    project.update(lateral_system=scfg["lateral_system"], R=scfg["R"], cd=scfg["Cd"], sys="other",
                   cs="", csAuto=True, Tan="", kAuto=True, sds=hz["sds"], sd1=hz["sd1"],
                   ie=hz["ie"], rc=hz["risk_category"])
    base = {"system": scfg["lateral"], "lateral_system": scfg["lateral_system"], "R": scfg["R"]}
    try:
        res = elf_engine.analyze(project, R=scfg["R"])
    except ValueError as e:
        return {**base, "error": str(e), "V_kips": None}
    bs, period = res.get("base_shear") or {}, res.get("period") or {}
    return {**base, "V_kips": bs.get("V"), "W_kips": bs.get("W"), "cs": bs.get("cs"),
            "T_s": period.get("T"), "sdc": (res.get("inputs") or {}).get("sdc"),
            "warnings": res.get("warnings") or []}


def _conditions(scheme: dict, lat: dict, scfg: dict, ctx: dict) -> list[str]:
    """Plain-language caveats for the client (DSH-7)."""
    out = []
    w = scheme["warnings"]
    unframed = sum("long-span or transfer framing needed" in x or "infeasible" in x for x in w)
    if unframed:
        out.append(f"{unframed} bay{'s' if unframed > 1 else ''} need long-span or transfer framing "
                   "beyond the firm's bay study; cost and weight there are estimates")
    low = [lv["level"] for lv in scheme["levels"]
           if lv["clear_height_ft"] < ctx["inp"]["constraints"]["min_ceiling_height_ft"] - 0.01]
    if low:
        out.append("Framing is deeper than the drawn story heights allow at " + ", ".join(low) +
                   "; those stories would need to be taller")
    if lat.get("error"):
        out.append("Lateral analysis did not run: " + lat["error"])
    elif lat.get("V_kips") is None:
        out.append("Lateral analysis gave no base shear; check the plan outline and story heights")
    sdc = ctx["hazard"]["sdc"]
    limit = (scfg.get("height_limit_ft") or {}).get(sdc or "")
    hn = _roof_height(ctx["state"])
    if limit and hn and hn > limit:
        out.append(f"At {hn:g} ft the building is above the {limit:g} ft ASCE 7 height limit for "
                   f"{scfg['lateral']} in Seismic Design Category {sdc}")
    return out


def _pros_cons(opts: list[dict], cfg: dict) -> None:
    """Best and worst of the three on each measure, then each system's general notes."""
    if len(opts) > 1:
        for key, (best, worst) in BEST_WORST.items():
            vals = [o["metrics"].get(key) for o in opts]
            known = [v for v in vals if v is not None]
            if len(known) < 2 or max(known) - min(known) <= 0.02 * max(abs(max(known)), 1e-9):
                continue  # within 2%: no real difference to point out
            for o, v in zip(opts, vals):
                if v == min(known):
                    o["pros"].append(best)
                elif v == max(known):
                    o["cons"].append(worst)
    for o in opts:
        s = cfg["systems"][o["key"]]
        o["pros"] += s.get("pros", [])
        o["cons"] = o["conditions"] + o["cons"] + s.get("cons", [])
