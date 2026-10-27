"""Rebuild a to-scale floor plan from a sketch reading.

Hand sketches are rarely drawn to scale, so the drawn proportions are not trusted.
The plan is split into spans between the x and y lines of the drawing ("ticks").
The written dimensions fix the span lengths; spans with no dimension are scaled
from the drawing at the median scale of the dimensions.
"""
import math

ORTHO_TOL_DEG = 12      # an edge within this angle of horizontal/vertical is snapped to it
TICK_TOL = 0.02         # lines closer than 2 % of the image size are treated as the same line
PRIOR_WEIGHT = 1e-7     # weight of the drawn length vs. the written dimension in the solve


def avg(a):
    return sum(a) / len(a)


def median(a):
    s = sorted(a)
    n = len(s)
    if not n:
        return None
    return s[(n - 1) // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def clean(v, nd=6):
    """Round off solver noise (299.9999999 -> 300)."""
    r = round(v, nd)
    return 0.0 if r == 0 else r


def orthogonalize(pts):
    """Make near-horizontal / near-vertical edges exactly horizontal / vertical."""
    n = len(pts)
    if n < 3:
        return [list(p) for p in pts]
    ux, uy = list(range(n)), list(range(n))

    def find(u, i):
        while u[i] != i:
            u[i] = u[u[i]]
            i = u[i]
        return i

    def join(u, a, b):
        u[find(u, a)] = find(u, b)

    t = math.tan(math.radians(ORTHO_TOL_DEG))
    for i in range(n):
        j = (i + 1) % n
        dx, dy = abs(pts[j][0] - pts[i][0]), abs(pts[j][1] - pts[i][1])
        if dy <= t * dx:
            join(uy, i, j)
        elif dx <= t * dy:
            join(ux, i, j)
    gx, gy = {}, {}
    for i in range(n):
        gx.setdefault(find(ux, i), []).append(pts[i][0])
        gy.setdefault(find(uy, i), []).append(pts[i][1])
    return [[avg(gx[find(ux, i)]), avg(gy[find(uy, i)])] for i in range(n)]


def cluster(vals, tol):
    out, g = [], []
    for v in sorted(vals):
        if g and v - g[-1] > tol:
            out.append(avg(g))
            g = []
        g.append(v)
    if g:
        out.append(avg(g))
    return out


def nearest_idx(ticks, v):
    if not ticks:
        return -1, float("inf")
    i = min(range(len(ticks)), key=lambda k: abs(ticks[k] - v))
    return i, abs(ticks[i] - v)


def snap(ticks, v, tol):
    i, d = nearest_idx(ticks, v)
    return ticks[i] if i >= 0 and d <= tol else v


def gauss(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        d = M[c][c]
        if abs(d) < 1e-15:
            continue
        for r in range(n):
            if r != c and M[r][c]:
                f = M[r][c] / d
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [0.0 if abs(M[i][i]) < 1e-15 else M[i][n] / M[i][i] for i in range(n)]


def solve_axis(ticks, cons, s):
    """Span lengths between consecutive ticks. cons: [{a, b, value}] = sum of spans a..b-1."""
    n = len(ticks) - 1
    if n < 1:
        return []
    N = [[0.0] * n for _ in range(n)]
    r = [0.0] * n
    for i in range(n):
        N[i][i] += PRIOR_WEIGHT
        r[i] += PRIOR_WEIGHT * (ticks[i + 1] - ticks[i]) * s
    for c in cons:
        for i in range(c["a"], c["b"]):
            for j in range(c["a"], c["b"]):
                N[i][j] += 1
            r[i] += c["value"]
    x = gauss(N, r)
    return [max(v, (ticks[i + 1] - ticks[i]) * s * 0.05, 1e-9) for i, v in enumerate(x)]


def mapper(ticks, cum, s):
    def f(v):
        L = len(ticks) - 1
        if L < 0:
            return v * s
        if v <= ticks[0]:
            return (v - ticks[0]) * s
        if v >= ticks[L]:
            return cum[L] + (v - ticks[L]) * s
        for i in range(L):
            if v <= ticks[i + 1]:
                t = (v - ticks[i]) / (ticks[i + 1] - ticks[i])
                return cum[i] + t * (cum[i + 1] - cum[i])
        return cum[L]
    return f


def fmt_n(v):
    return ("%.4f" % v).rstrip("0").rstrip(".")


def tile(cs, lo, hi, exclude=None):
    """Dimensions that sit end to end and exactly cover tick span lo..hi (fewest pieces), or None."""
    best = None

    def walk(pos, used):
        nonlocal best
        if pos == hi:
            if best is None or len(used) < len(best):
                best = list(used)
            return
        for c in cs:
            if c is not exclude and c["a"] == pos and c["b"] <= hi and c not in used:
                used.append(c)
                walk(c["b"], used)
                used.pop()
    walk(lo, [])
    return best


def dim_orient(d):
    if d.get("orient") in ("h", "v"):
        return d["orient"]
    dx = abs(d["p2"][0] - d["p1"][0])
    dy = abs(d["p2"][1] - d["p1"][1])
    return "h" if dx >= dy * 2 else "v" if dy >= dx * 2 else "a"


def rebuild(rd):
    """rd: reading in image pixels (see reader.normalize_reading). Returns the to-scale plan."""
    W, H = rd["W"], rd["H"]
    tolx, toly = max(8, TICK_TOL * W), max(8, TICK_TOL * H)
    warnings = []
    outline_px = orthogonalize(rd["outline"])
    cores_px = [orthogonalize(c) for c in rd["cores"]]
    active = [d for d in rd["dims"] if d.get("value") and d["value"] > 0]

    ratios = []
    for d in active:
        o = dim_orient(d)
        span = (abs(d["p2"][0] - d["p1"][0]) if o == "h" else abs(d["p2"][1] - d["p1"][1]) if o == "v"
                else math.dist(d["p1"], d["p2"]))
        d["span_px"] = span
        if span > 2:
            ratios.append(d["value"] / span)
    s = median(ratios)
    scaled = s is not None
    if not scaled:
        s = 1.0
        warnings.append("No dimensions were read, so the plan is NOT to scale (1 unit = 1 pixel).")

    tx = cluster([p[0] for p in outline_px] + [p[0] for c in cores_px for p in c] + rd["grids_x"] +
                 [p[0] for p in rd["columns"]], tolx)
    ty = cluster([p[1] for p in outline_px] + [p[1] for c in cores_px for p in c] + rd["grids_y"] +
                 [p[1] for p in rd["columns"]], toly)
    ex, ey = [], []
    for d in active:
        o = dim_orient(d)
        if o == "h":
            ex += [v for v in (d["p1"][0], d["p2"][0]) if nearest_idx(tx, v)[1] > tolx * 2.5]
        if o == "v":
            ey += [v for v in (d["p1"][1], d["p2"][1]) if nearest_idx(ty, v)[1] > toly * 2.5]
    if ex:
        tx = cluster(tx + ex, tolx)
    if ey:
        ty = cluster(ty + ey, toly)

    cons = {"h": [], "v": []}
    for d in active:
        o = dim_orient(d)
        d["orient"] = o
        if o == "a":
            d["skip"] = "diagonal dimension, not used for scaling"
            continue
        T, k = (tx, 0) if o == "h" else (ty, 1)
        a, b = sorted((nearest_idx(T, d["p1"][k])[0], nearest_idx(T, d["p2"][k])[0]))
        if a == b:
            d["skip"] = "both ends land on the same line"
            continue
        d["a"], d["b"] = a, b
        cons[o].append(d)

    ivx, ivy = solve_axis(tx, cons["h"], s), solve_axis(ty, cons["v"], s)
    cumx, cumy = [0.0], [0.0]
    for v in ivx:
        cumx.append(cumx[-1] + v)
    for v in ivy:
        cumy.append(cumy[-1] + v)
    Wt, Ht = cumx[-1], cumy[-1]
    fx, fyr = mapper(tx, cumx, s), mapper(ty, cumy, s)

    def fy(v):
        return Ht - fyr(v)

    def P(p):
        return [clean(fx(snap(tx, p[0], tolx)), 4), clean(fy(snap(ty, p[1], toly)), 4)]

    for o, iv in (("h", ivx), ("v", ivy)):
        for d in cons[o]:
            d["solved"] = clean(sum(iv[d["a"]:d["b"]]), 4)
            d["ok"] = abs(d["solved"] - d["value"]) <= max(0.001 * d["value"], 1e-6)
            other = avg([d["p1"][1], d["p2"][1]]) if o == "h" else avg([d["p1"][0], d["p2"][0]])
            d["lo"], d["hi"] = (clean(cumx[d["a"]], 4), clean(cumx[d["b"]], 4)) if o == "h" else \
                (clean(Ht - cumy[d["b"]], 4), clean(Ht - cumy[d["a"]], 4))
            d["at"] = clean(fy(snap(ty, other, toly * 2.5)), 4) if o == "h" else clean(fx(snap(tx, other, tolx * 2.5)), 4)

    # chain checks: does a run of dimensions add up to the dimension that spans them?
    explained = set()
    for o in ("h", "v"):
        for d in cons[o]:
            if d["b"] - d["a"] < 2:
                continue
            chain = tile(cons[o], d["a"], d["b"], exclude=d)
            if not chain:
                continue
            total = sum(c["value"] for c in chain)
            if abs(total - d["value"]) > max(0.001 * d["value"], 1e-6):
                names = " + ".join(c["id"] for c in chain)
                warnings.append("%s = %s, but %s (%s) = %s: a difference of %s. Both were averaged (solved %s); "
                                "check which number is right." % (
                                    names, fmt_n(total), d["id"], d.get("text") or fmt_n(d["value"]), fmt_n(d["value"]),
                                    fmt_n(abs(total - d["value"])), fmt_n(d["solved"])))
                explained.update([d["id"]] + [c["id"] for c in chain])
    for o in ("h", "v"):
        for d in cons[o]:
            if not d["ok"] and d["id"] not in explained:
                warnings.append('%s "%s" does not agree with the other dimensions (solved %s).'
                                % (d["id"], d.get("text") or fmt_n(d["value"]), fmt_n(d["solved"])))

    def prov(n, cs):
        p = ["est"] * n
        for c in cs:
            for i in range(c["a"], c["b"]):
                if c["b"] - c["a"] == 1:
                    p[i] = "given"
                elif p[i] == "est":
                    p[i] = "derived"
        return p
    pvx, pvy = prov(len(ivx), cons["h"]), prov(len(ivy), cons["v"])
    if scaled:
        n_est = pvx.count("est") + pvy.count("est")
        if n_est:
            warnings.append("%d span(s) have no dimension and were scaled from the drawing." % n_est)

    vert_ticks = [(nearest_idx(tx, p[0])[0], nearest_idx(ty, p[1])[0]) for p in outline_px]
    gx, gy = sorted(rd["grids_x"]), sorted(rd["grids_y"])
    if len(outline_px) < 3:
        warnings.insert(0, "No floor outline was found.")
    return {
        "scaled": scaled, "scale": s, "W": clean(Wt, 4), "H": clean(Ht, 4),
        "outline": [P(p) for p in outline_px], "outline_px": outline_px, "vert_ticks": vert_ticks,
        "cores": [[P(p) for p in c] for c in cores_px], "columns": [P(p) for p in rd["columns"]],
        "grids_x": [{"label": str(i + 1), "pos": clean(fx(snap(tx, g, tolx)), 4)} for i, g in enumerate(gx)],
        "grids_y": [{"label": chr(65 + i), "pos": clean(fy(snap(ty, g, toly)), 4)} for i, g in enumerate(gy)],
        "dims": rd["dims"], "cons": cons, "tol": (tolx, toly), "warnings": warnings,
    }


def plate_properties(P):
    """Area, perimeter, centroid, second moments, edges as vectors, corner types."""
    n = len(P)
    if n < 3:
        return None
    signed = sum(P[i][0] * P[(i + 1) % n][1] - P[(i + 1) % n][0] * P[i][1] for i in range(n)) / 2
    ccw = signed > 0
    cx = sum((P[i][0] + P[(i + 1) % n][0]) * (P[i][0] * P[(i + 1) % n][1] - P[(i + 1) % n][0] * P[i][1])
             for i in range(n)) / (6 * signed)
    cy = sum((P[i][1] + P[(i + 1) % n][1]) * (P[i][0] * P[(i + 1) % n][1] - P[(i + 1) % n][0] * P[i][1])
             for i in range(n)) / (6 * signed)
    Ixx = Iyy = Ixy = 0.0
    for i in range(n):
        x0, y0 = P[i][0] - cx, P[i][1] - cy
        x1, y1 = P[(i + 1) % n][0] - cx, P[(i + 1) % n][1] - cy
        a = x0 * y1 - x1 * y0
        Ixx += (y0 * y0 + y0 * y1 + y1 * y1) * a
        Iyy += (x0 * x0 + x0 * x1 + x1 * x1) * a
        Ixy += (x0 * y1 + 2 * x0 * y0 + 2 * x1 * y1 + x1 * y0) * a
    sg = 1 if ccw else -1
    Ixx, Iyy, Ixy = sg * Ixx / 12, sg * Iyy / 12, sg * Ixy / 24

    edges = []
    for i in range(n):
        a, b = P[i], P[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        nx, ny = (uy, -ux) if ccw else (-uy, ux)
        faces = ("east" if nx > 0 else "west") if abs(nx) > 0.97 else \
                ("north" if ny > 0 else "south") if abs(ny) > 0.97 else "angled"
        edges.append({
            "id": "E%d" % (i + 1), "start": "C%d" % (i + 1), "end": "C%d" % ((i + 1) % n + 1),
            "start_xy": a, "end_xy": b, "vector": [clean(dx), clean(dy)],
            "unit_vector": [clean(ux), clean(uy)], "outward_normal": [clean(nx), clean(ny)],
            "length": clean(L), "angle_deg": clean(math.degrees(math.atan2(dy, dx)) % 360, 3),
            "orientation": "horizontal" if abs(dy) < 1e-9 else "vertical" if abs(dx) < 1e-9 else "angled",
            "faces": faces, "midpoint": [clean((a[0] + b[0]) / 2), clean((a[1] + b[1]) / 2)],
        })
    verts = []
    for i, p in enumerate(P):
        a, b = P[i - 1], P[(i + 1) % n]
        cr = (p[0] - a[0]) * (b[1] - p[1]) - (p[1] - a[1]) * (b[0] - p[0])
        verts.append({"id": "C%d" % (i + 1), "xy": p, "corner": "convex" if (cr > 0) == ccw else "re-entrant"})
    xs, ys = [p[0] for p in P], [p[1] for p in P]
    bw, bh = max(xs) - min(xs), max(ys) - min(ys)
    area = abs(signed)
    return {
        "area": clean(area), "perimeter": clean(sum(e["length"] for e in edges)),
        "bounding_box": {"min": [min(xs), min(ys)], "max": [max(xs), max(ys)], "width_x": clean(bw), "depth_y": clean(bh)},
        "bounding_box_area": clean(bw * bh), "fill_ratio": round(area / (bw * bh), 4) if bw * bh else None,
        "aspect_ratio": round(bw / bh, 3) if bh else None,
        "centroid": [clean(cx, 3), clean(cy, 3)],
        "second_moment_about_centroid": {"Ixx": round(Ixx), "Iyy": round(Iyy), "Ixy": round(Ixy)},
        "vertex_count": n, "edge_count": n, "winding": "counterclockwise" if ccw else "clockwise",
        "re_entrant_corners": [v["id"] for v in verts if v["corner"] == "re-entrant"],
        "edges": edges, "vertices": verts,
    }


def edge_sources(plan, props):
    """Which written dimension sets each edge's length."""
    out = {}
    n = len(plan["outline"])
    tolx, toly = plan["tol"]
    for i, e in enumerate(props["edges"]):
        (ixa, iya), (ixb, iyb) = plan["vert_ticks"][i], plan["vert_ticks"][(i + 1) % n]
        pa, pb = plan["outline_px"][i], plan["outline_px"][(i + 1) % n]
        if e["orientation"] == "horizontal":
            lo, hi, cs, pos, k, tol = min(ixa, ixb), max(ixa, ixb), plan["cons"]["h"], pa[1], 1, toly
        elif e["orientation"] == "vertical":
            lo, hi, cs, pos, k, tol = min(iya, iyb), max(iya, iyb), plan["cons"]["v"], pa[0], 0, tolx
        else:
            out[e["id"]] = (None, "angled edge, scaled from the drawing")
            continue
        exact = [d for d in cs if d["a"] == lo and d["b"] == hi]
        if exact:
            on_edge = [d for d in exact if abs(avg([d["p1"][k], d["p2"][k]]) - pos) <= tol * 2.5]
            d = (on_edge or exact)[0]
            out[e["id"]] = (d["id"], "written" if on_edge else "same span as %s" % d["id"])
            continue
        chain = tile(cs, lo, hi)
        if chain:
            out[e["id"]] = ("+".join(c["id"] for c in chain), "chain " + " + ".join(c["id"] for c in chain))
            continue
        cover = [d for d in cs if d["a"] <= lo and d["b"] >= hi]
        if cover:
            d = min(cover, key=lambda c: c["b"] - c["a"])
            out[e["id"]] = (d["id"], "part of the %s chain" % d["id"])
        else:
            out[e["id"]] = (None, "not dimensioned, scaled from the drawing")
    return out
