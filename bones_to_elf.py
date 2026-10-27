"""Convert a Bones gravity-input file into a project for the Seismic ELF braced-frame dashboard.

Usage:
    python bones_to_elf.py bones-gravity-inputs.json --self-weight-psf 55
    python bones_to_elf.py bones-gravity-inputs.json --self-weight-psf 55 --sds 0.30 --sd1 0.12

Then load the output file with the dashboard's Open... button, or POST it to the API (api.py).

Lateral system (the only one in scope): steel system not specifically detailed for
seismic resistance, ASCE 7-22 Table 12.2-1 row H. R = 3, Omega0 = 3, Cd = 3, permitted
in SDC B and C only.

Seismic weight (ASCE 7-22 12.7.2) for each load area = structural self-weight + SDL +
facade spread over the floor. Live load is left out. The office partition allowance is
already in SDL, so it stays in the weight.

Uses only the standard library.
"""

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

from elf_engine import IE_BY_RISK, cs_of, cu_of, k_from_t, sdc_of

R, OMEGA0, CD = 3.0, 3.0, 3.0
CT, X_EXP = 0.02, 0.75  # Table 12.8-2, all other structural systems


def polygon_stats(verts):
    """Area and perimeter of a closed polygon given as [[x, y], ...]."""
    area = per = 0.0
    for (x1, y1), (x2, y2) in zip(verts, verts[1:] + verts[:1]):
        area += x1 * y2 - x2 * y1
        per += math.hypot(x2 - x1, y2 - y1)
    return abs(area) / 2, per


def rect_area(r):
    return abs(r["x2"] - r["x1"]) * abs(r["y2"] - r["y1"])


def convert(b, self_weight_psf, roof_self_weight_psf=None, roof_extra_psf=0.0, parapet_ft=0.0,
            sds=None, sd1=None, brace_area=10.0, drop_frames=(), source_name=""):
    """Build a dashboard project from a Bones gravity-input dict.

    Returns (project, summary). Raises ValueError for bad input, and for SDC D, where the
    R = 3 system is not permitted.
    """
    warnings = []
    drop_frames = list(drop_frames)

    # ---- plan ----
    verts = [[float(x), float(y)] for x, y in b["geometry"]["plan"]["verts"]]
    area, perim = polygon_stats(verts)

    # ---- levels: the lowest level is the base; every level above it is a seismic level ----
    base = min(L["elevation_ft"] for L in b["levels"])
    elevated = sorted((L for L in b["levels"] if L["elevation_ft"] > base), key=lambda L: L["elevation_ft"])
    if not elevated:
        raise ValueError("No levels above the base.")
    n = len(elevated)
    heights = [L["elevation_ft"] - base for L in elevated]
    stories = [h - h0 for h, h0 in zip(heights, [0.0] + heights[:-1])]

    # ---- seismic weight by load area ----
    facade = (b.get("perimeter") or {}).get("facade_weight_psf") or 0.0
    sdl_by_use = {u["id"]: u.get("SDL_psf") for u in b.get("uses", [])}
    roof_sw = self_weight_psf if roof_self_weight_psf is None else roof_self_weight_psf
    levels, lv, weights = [], [], []
    for j, L in enumerate(elevated):
        is_roof = j == n - 1
        # facade mass on its tributary height: half the story below plus half above (parapet at the roof)
        trib = stories[j] / 2 + (parapet_ft if is_roof else stories[j + 1] / 2)
        facade_psf = facade * trib * perim / area
        sw = roof_sw if is_roof else self_weight_psf
        extra = roof_extra_psf if is_roof else 0.0

        m = b["loadMaps"][L["loadMap"]]
        ids = []
        for q in m["laIds"]:
            sdl = q.get("SDL_psf", sdl_by_use.get(q["id"]))
            if sdl is None:
                raise ValueError(f"Load area ID '{q['id']}' on {L['name']} has no SDL_psf.")
            ids.append({"id": q["id"], "psf": round(sw + sdl + facade_psf + extra, 2)})
        psf = {q["id"]: q["psf"] for q in ids}
        rects = [{k: r[k] for k in ("id", "x1", "y1", "x2", "y2")} for r in m["la"]]
        missing = {r["id"] for r in rects} - psf.keys()
        if missing:
            raise ValueError(f"{L['name']}: load areas use IDs with no entry in laIds: {', '.join(sorted(missing))}.")

        covered = sum(rect_area(r) for r in rects)
        if abs(covered - area) > 0.01:
            warnings.append(f"{L['name']}: load areas cover {covered:,.0f} of {area:,.0f} sf. "
                            "The dashboard ignores load areas that don't cover the whole plan.")
        w = sum(psf[r["id"]] * rect_area(r) for r in rects) / 1000
        weights.append(w)
        levels.append({"name": L["name"], "h": heights[j], "psf": round(w * 1000 / area, 2), "la": True})
        lv.append({"la": rects, "laIds": ids, "laActive": ids[0]["id"] if ids else ""})

    # ---- braced frames ----
    A = brace_area
    frames, labels = [], set()
    for f in b["lateralSystem"]["lfrs"]:
        labels.add(f["label"])
        if f["label"] in drop_frames:
            continue
        x1, x2 = sorted((f["x1"], f["x2"]))
        y1, y2 = sorted((f["y1"], f["y2"]))
        if x1 != x2 and y1 != y2:
            raise ValueError(f"Frame {f['label']} is not parallel to X or Y.")
        frames.append({"label": f["label"], "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                       "A": A, "As": [A] * n, "up": "b", "alt": False, "type": "single", "mid": "top"})
    for lab in set(drop_frames) - labels:
        warnings.append(f"Drop frame {lab}: no frame with that label.")

    # ---- period, SDC and Cs ----
    risk = b["project"].get("risk_category", "II")
    if risk not in IE_BY_RISK:
        raise ValueError(f"Unknown risk category '{risk}'.")
    ie = IE_BY_RISK[risk]
    hz = (b.get("hazard") or {}).get("seismic") or {}
    sds = sds if sds is not None else hz.get("SDS")
    sd1 = sd1 if sd1 is not None else hz.get("SD1")
    hn = heights[-1]
    ta = CT * hn ** X_EXP
    k = k_from_t(ta)

    sdc = cs = None
    if sds is not None and sd1 is not None:
        sdc = sdc_of(sds, sd1, risk)
        if sdc == "D":
            raise ValueError(f"SDC D (SDS = {sds}, SD1 = {sd1}, Risk Category {risk}). The R = 3 'not specifically "
                             "detailed' steel system is not permitted in SDC D to F (Table 12.2-1).")
        cs = round(cs_of(sds, sd1, ta, ie, R), 4)
        if sdc == "A":
            warnings.append("SDC A: 11.7 applies instead of the ELF procedure. The output is for reference only.")
    else:
        warnings.append("SDS/SD1 not given: Cs, SDS and SD1 are left blank, so the dashboard asks for them.")
    warnings.append("Not included: roof snow and rooftop equipment (roof extra psf), and the core slab opening.")

    project = {
        "project": b["project"].get("name", ""), "by": "", "date": date.today().isoformat(),
        "n": n, "cs": cs, "ie": ie, "sds": sds, "sd1": sd1,
        "sys": "other", "ct": CT, "xexp": X_EXP, "Tan": "", "k": round(k, 3), "kAuto": True,
        "fillH": max(set(stories), key=stories.count), "fillP": levels[n // 2]["psf"],
        "levels": levels,
        "plan": {"verts": verts, "closed": True, "lfrs": frames, "grid": 1, "aDef": A,
                 "kStory": 1, "padLv": 1, "lv": lv},
        # collectors need Omega0 in SDC C and up (12.10.2.1); col is not merged with defaults, so give every key
        "col": {"lvl": None, "line": "", "om": sdc not in ("A", "B"), "omega": OMEGA0, "method": "rigid", "acc": "env",
                "s": None},
        "_source": {
            "from": source_name,
            "system": "Steel system not specifically detailed for seismic resistance (ASCE 7-22 Table 12.2-1 H)",
            "R": R, "Omega0": OMEGA0, "Cd": CD, "SDC": sdc,
            "self_weight_psf": self_weight_psf, "roof_self_weight_psf": roof_sw,
            "roof_extra_psf": roof_extra_psf, "parapet_ft": parapet_ft,
            "dropped_frames": drop_frames,
        },
    }
    summary = {
        "area": area, "perimeter": perim, "facade_psf": facade,
        "levels": [{"name": L["name"], "h": L["h"], "psf": L["psf"], "w": w} for L, w in zip(levels, weights)],
        "W": sum(weights), "hn": hn, "Ta": ta, "k": k, "Cu": cu_of(sd1) if sd1 is not None else None,
        "sdc": sdc, "ie": ie, "cs": cs, "V": cs * sum(weights) if cs is not None else None,
        "frames": [f["label"] for f in frames], "warnings": warnings,
    }
    return project, summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="Bones gravity-input JSON")
    ap.add_argument("-o", "--output", type=Path, help="dashboard project JSON (default: <input>-elf.json)")
    ap.add_argument("--self-weight-psf", type=float, required=True,
                    help="structural self-weight of a typical floor (slab/deck + framing + columns)")
    ap.add_argument("--roof-self-weight-psf", type=float, help="roof self-weight (default: same as floors)")
    ap.add_argument("--roof-extra-psf", type=float, default=0.0,
                    help="other roof seismic weight: rooftop equipment, 20%% of pf when pf > 30 psf")
    ap.add_argument("--parapet-ft", type=float, default=0.0, help="parapet height added to the roof facade")
    ap.add_argument("--sds", type=float, help="SDS in g (overrides the file)")
    ap.add_argument("--sd1", type=float, help="SD1 in g (overrides the file)")
    ap.add_argument("--brace-area", type=float, default=10.0, help="brace area for every frame, in^2")
    ap.add_argument("--drop-frame", action="append", default=[], metavar="LABEL",
                    help="leave out a frame, e.g. --drop-frame Y2 (repeatable)")
    args = ap.parse_args()

    try:
        project, s = convert(json.loads(args.input.read_text()), args.self_weight_psf,
                             roof_self_weight_psf=args.roof_self_weight_psf, roof_extra_psf=args.roof_extra_psf,
                             parapet_ft=args.parapet_ft, sds=args.sds, sd1=args.sd1, brace_area=args.brace_area,
                             drop_frames=args.drop_frame, source_name=args.input.name)
    except ValueError as e:
        sys.exit(f"{e} No file written.")

    out = args.output or args.input.with_name(args.input.stem + "-elf.json")
    out.write_text(json.dumps(project, indent=2))

    src = project["_source"]
    print(f"Plan {s['area']:,.0f} sf, perimeter {s['perimeter']:,.0f} ft, facade {s['facade_psf']} psf")
    print(f"Self-weight {src['self_weight_psf']} psf floors, {src['roof_self_weight_psf']} psf roof "
          "(replace with the gravity optimizer's output)")
    print()
    print(f"{'Level':<12}{'h (ft)':>8}{'psf':>9}{'w (k)':>10}")
    for L in s["levels"]:
        print(f"{L['name']:<12}{L['h']:>8.1f}{L['psf']:>9.2f}{L['w']:>10.1f}")
    print(f"{'W':<29}{s['W']:>10.1f}")
    print()
    print(f"hn = {s['hn']:.1f} ft, Ta = {CT}*hn^{X_EXP} = {s['Ta']:.3f} s, k = {s['k']:.3f}")
    if s["Cu"] is not None:
        print(f"Cu = {s['Cu']:.2f} (only caps an analysis period; Ta is used here)")
    if s["cs"] is not None:
        print(f"SDC {s['sdc']}, Ie = {s['ie']}, R = {R:g}: Cs = {s['cs']}, V = Cs*W = {s['V']:,.1f} kips")
    print(f"Frames: {', '.join(s['frames'])} (A = {args.brace_area:g} in^2 each), Omega0 = {OMEGA0:g}")
    print()
    for w in s["warnings"]:
        print("NOTE: " + w)
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
