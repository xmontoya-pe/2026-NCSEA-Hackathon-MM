"""Convert a Sketch Reader plan (sketch-plan/0.2) into a Module 2 project file.

Module 2 (SEISMIC ELF BraceFrame) expects, per the plan-import handoff, section 4:
  - lengths in feet, Y up, the lower-left of the outline at (0, 0)
  - an orthogonal, closed outline with clean corners and no self-intersection
  - levels as cumulative heights, one entry per elevated level up to the roof
The checks below follow section 4.5 of that handoff. Nothing is silently "fixed"
except what the handoff says the prequel must do (units, origin, order, cleanup);
every such change is reported in `changes`.
"""

FT_PER = {"ft": 1.0, "m": 1 / 0.3048, "mm": 1 / 304.8}
EPS = 1e-6
DEFAULT_PSF = 150   # Module 3's psf column; the engineer sets the real value later


def rnd(v):
    """Module 2 stores values rounded to 1e-6 ft."""
    r = round(v, 6)
    return 0.0 if r == 0 else r


def signed_area(P):
    n = len(P)
    return sum(P[i][0] * P[(i + 1) % n][1] - P[(i + 1) % n][0] * P[i][1] for i in range(n)) / 2


def cleanup(P):
    """Drop repeated corners and corners in the middle of a straight edge. Returns (points, removed count)."""
    pts = [p for i, p in enumerate(P) if i == 0 or abs(p[0] - P[i - 1][0]) > EPS or abs(p[1] - P[i - 1][1]) > EPS]
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) <= EPS and abs(pts[0][1] - pts[-1][1]) <= EPS:
        pts.pop()
    removed = len(P) - len(pts)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if abs(cross) <= EPS * max(1.0, abs(c[0] - a[0]) + abs(c[1] - a[1])):
                pts.pop(i)
                removed += 1
                changed = True
                break
    return pts, removed


def _seg_cross(p, q, r, s):
    """True if segments pq and rs intersect (including touching)."""
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) <= EPS else (1 if v > 0 else -1)

    def on(a, b, c):
        return min(a[0], b[0]) - EPS <= c[0] <= max(a[0], b[0]) + EPS and min(a[1], b[1]) - EPS <= c[1] <= max(a[1], b[1]) + EPS

    o1, o2, o3, o4 = orient(p, q, r), orient(p, q, s), orient(r, s, p), orient(r, s, q)
    if o1 != o2 and o3 != o4:
        return True
    return (o1 == 0 and on(p, q, r)) or (o2 == 0 and on(p, q, s)) or (o3 == 0 and on(r, s, p)) or (o4 == 0 and on(r, s, q))


def self_intersects(P):
    n = len(P)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue   # neighbouring edges share a corner
            if _seg_cross(P[i], P[(i + 1) % n], P[j], P[(j + 1) % n]):
                return True
    return False


def roof_snow(snow):
    """Flat-roof snow load from the engineer's inputs, ASCE 7-22 Eq. 7.3-1: pf = 0.7 Ce Ct pg.
    snow: {"pg": psf, "Ce": exposure factor, "Ct": thermal factor}. Returns None when pg is not entered."""
    if not snow or snow.get("pg") in (None, ""):
        return None
    pg, ce, ct = float(snow["pg"]), float(snow.get("Ce") or 1.0), float(snow.get("Ct") or 1.0)
    if pg < 0 or not 0.5 <= ce <= 1.5 or not 0.8 <= ct <= 1.5:
        raise ValueError("Snow inputs are out of range: pg must be 0 or more, Ce 0.5 to 1.5, Ct 0.8 to 1.5.")
    return {"pg_psf": pg, "Ce": ce, "Ct": ct, "pf_psf": round(0.7 * ce * ct * pg, 2),
            "basis": "ASCE 7-22 Eq. 7.3-1, balanced flat-roof snow load. Minimum roof snow, drift, sliding "
                     "and rain-on-snow are not included; check them in design."}


def to_module2(plan, name=None, levels=None, storey_height=None, psf=DEFAULT_PSF, snow=None):
    """plan: the dict written to <name>-plan.json. Returns {"project", "checks", "changes", "ready"}."""
    units = (plan.get("units") or {}).get("length", "ft")
    f = FT_PER.get(units, 1.0)
    changes, checks = [], []

    def check(key, ok, text, level="error"):
        checks.append({"id": key, "ok": bool(ok), "level": "ok" if ok else level, "text": text})

    pts = [[p[0] * f, p[1] * f] for p in (plan.get("outline") or [])]
    if units != "ft":
        changes.append("Converted lengths from %s to ft." % units)

    pts, removed = cleanup(pts)
    if removed:
        changes.append("Removed %d repeated or mid-edge corner%s." % (removed, "s" * (removed > 1)))

    angled = [i for i in range(len(pts))
              if abs(pts[(i + 1) % len(pts)][0] - pts[i][0]) > EPS and abs(pts[(i + 1) % len(pts)][1] - pts[i][1]) > EPS]

    if pts:
        x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
        if abs(x0) > EPS or abs(y0) > EPS:
            changes.append("Moved the lower-left corner to (0, 0).")
        pts = [[p[0] - x0, p[1] - y0] for p in pts]
    if len(pts) >= 3 and signed_area(pts) < 0:
        pts.reverse()
        changes.append("Reversed the corner order to counter-clockwise.")
    if pts:
        start = min(range(len(pts)), key=lambda i: (round(pts[i][1], 6), round(pts[i][0], 6)))
        if start:
            pts = pts[start:] + pts[:start]
            changes.append("Started the outline at the lower-left corner.")
    verts = [[rnd(p[0]), rnd(p[1])] for p in pts]

    # section 4.5 checks
    check("corners", len(verts) >= 4, "Outline has %d corners (at least 4 needed)." % len(verts))
    check("orthogonal", not angled and len(verts) >= 4,
          "Every edge is horizontal or vertical." if not angled else
          "%d edge%s angled. Module 2 needs steps instead of angled edges; redraw those edges in the drawing pad."
          % (len(angled), " is" if len(angled) == 1 else "s are"))
    check("clean", True, "No repeated or mid-edge corners.")
    check("simple", len(verts) >= 3 and not self_intersects(verts),
          "Edges meet only at their corners." if len(verts) >= 3 and not self_intersects(verts) else "The outline crosses itself.")
    check("origin", True, "Lower-left corner sits at (0, 0).")

    dims = plan.get("dimensions") or []
    bad = [d for d in dims if d.get("agrees") is False]
    check("dimensions", dims and not bad,
          "All %d written dimensions agree." % len(dims) if dims and not bad else
          "No dimensions were read, so the plan is not to scale." if not dims else
          "%d dimension%s disagree%s; confirm which number is right." % (len(bad), "s" * (len(bad) > 1), "" if len(bad) > 1 else "s"),
          level="error" if not dims else "warning")

    lv = plan.get("levels") or {}
    n = int(levels or lv.get("count") or 1)
    h = float(storey_height or lv.get("storey_height") or 12.0) * (f if not storey_height else 1.0)
    from_sketch = lv.get("source") in ("sketch", "entered") or bool(levels or storey_height)
    check("levels", from_sketch,
          "%d stor%s at %s ft." % (n, "y" if n == 1 else "ies", ("%.2f" % h).rstrip("0").rstrip("."))
          if from_sketch else "Stories and story height were not on the sketch; enter them.", level="warning")
    sn = roof_snow(snow)   # snow is normally entered on step 3, with the other loads; accepted here for API callers
    if sn:
        check("snow", True, "Ground snow load %s psf; flat-roof snow load pf = %s psf." % (_n(sn["pg_psf"]), _n(sn["pf_psf"])))
    check("frames", False, "Braced frames are not read from sketches; add them in the drawing pad.", level="info")

    ready = all(c["ok"] or c["level"] in ("warning", "info") for c in checks)
    project = {
        "project": name or (plan.get("source") or {}).get("image", "Imported plan"),
        "n": n,
        "levels": [{"name": "", "h": rnd(h * (i + 1)), "psf": psf, "la": False} for i in range(n)],
        "plan": {"verts": verts, "closed": True, "grid": 1, "lfrs": []},
        # Kept for Bones' other steps. Module 2 ignores keys it does not know.
        "bones": {
            "source": plan.get("source"),
            "area_sf": rnd(abs(signed_area(verts))) if len(verts) >= 3 else None,
            "grids": _scaled_grids(plan, f, x0 if pts else 0, y0 if pts else 0),
            "columns": [[rnd(c[0] * f - (x0 if pts else 0)), rnd(c[1] * f - (y0 if pts else 0))] for c in plan.get("columns") or []],
            "cores": [[[rnd(p[0] * f - x0), rnd(p[1] * f - y0)] for p in c] for c in plan.get("cores") or []] if pts else [],
            "warnings": plan.get("warnings") or [],
            "snow": sn,
        },
    }
    return {"project": project, "checks": checks, "changes": changes, "ready": ready}


def _scaled_grids(plan, f, x0, y0):
    g = plan.get("grids") or {}
    return {"x": [{"label": q["label"], "pos": rnd(q["pos"] * f - x0)} for q in g.get("x", [])],
            "y": [{"label": q["label"], "pos": rnd(q["pos"] * f - y0)} for q in g.get("y", [])]}


def _n(v):
    return ("%.2f" % v).rstrip("0").rstrip(".")
