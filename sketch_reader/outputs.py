"""Turn a sketch reading into the output files: summary text, JSON, CSV, DXF, SVG and an overlay image."""
import csv
import datetime
import json
import os

from PIL import ImageDraw, ImageFont

from . import geometry as G
from .reader import load_image, normalize_reading

ALT = {"ft": ("m", 0.3048), "m": ("ft", 1 / 0.3048), "mm": ("m", 0.001)}
AREA = {"ft": "ft2", "m": "m2", "mm": "mm2"}
DEFAULT_LEVEL = {"ft": (12.0, 0.83), "m": (3.6, 0.25), "mm": (3600.0, 250.0)}
COLUMN = {"ft": 1.33, "m": 0.4, "mm": 400.0}


def num(v, sep=False, nd=3):
    if v is None:
        return "-"
    if abs(v - round(v)) < 1e-6:
        return format(int(round(v)), "," if sep else "")
    s = format(v, (",." if sep else ".") + "%df" % nd).rstrip("0").rstrip(".")
    return s


def pt(p):
    return "(%s, %s)" % (num(p[0]), num(p[1]))


def table(headers, rows, gap=3):
    cols = list(zip(*([headers] + rows))) if rows else [[h] for h in headers]
    w = [max(len(str(c)) for c in col) for col in cols]
    line = lambda r: (" " * gap).join(str(c).ljust(w[i]) for i, c in enumerate(r)).rstrip()
    return "\n".join([line(headers)] + [line(r) for r in rows])


def section(n, title):
    t = "%d. %s" % (n, title)
    return "\n\n%s\n%s" % (t, "-" * len(t))


def build(image_path, raw, levels=None, storey_height=None):
    """Everything the writers need, from the photo and the raw reading."""
    img = load_image(image_path)
    W, H = img.size
    rd = normalize_reading(raw, W, H)
    plan = G.rebuild(rd)
    props = G.plate_properties(plan["outline"])
    units = rd["units"]
    dh, dt = DEFAULT_LEVEL[units]
    lv_read = rd["levels"]
    lv = {
        "count": levels or lv_read["count"] or 1,
        "storey_height": storey_height or lv_read["storey_height"] or dh,
        "slab_thickness": dt,
        "source": "entered" if (levels or storey_height) else "sketch" if (lv_read["count"] or lv_read["storey_height"]) else
        "default (not on the sketch)",
    }
    srcs = G.edge_sources(plan, props) if props else {}
    return {"image": img, "image_path": image_path, "raw": raw, "rd": rd, "plan": plan, "props": props,
            "units": units, "levels": lv, "edge_src": srcs}


def corner_at(B, x, y):
    for v in (B["props"] or {}).get("vertices", []):
        if abs(v["xy"][0] - x) < 1e-6 and abs(v["xy"][1] - y) < 1e-6:
            return v["id"]
    return None


def dim_between(B, d):
    if "lo" not in d:
        return d.get("skip", "-")
    if d["orient"] == "h":
        a, b = corner_at(B, d["lo"], d["at"]), corner_at(B, d["hi"], d["at"])
        span = "x %s to %s at y = %s" % (num(d["lo"]), num(d["hi"]), num(d["at"]))
    else:
        a, b = corner_at(B, d["at"], d["lo"]), corner_at(B, d["at"], d["hi"])
        span = "y %s to %s at x = %s" % (num(d["lo"]), num(d["hi"]), num(d["at"]))
    return ("%s to %s, " % (a, b) if a and b else "") + span


# ---------------------------------------------------------------- summary text
def summary_text(B):
    u, plan, P = B["units"], B["plan"], B["props"]
    alt, k = ALT[u]
    rd, lv = B["rd"], B["levels"]
    meta = B["raw"].get("_meta", {})
    out = ["SKETCH PLAN SUMMARY", "==================="]
    out.append(table(["", ""], [
        ["Source:", os.path.basename(B["image_path"]) + "  (%d x %d px)" % B["image"].size],
        ["Read on:", datetime.date.today().isoformat() + ("  with " + meta["model"] if meta.get("model") else "")],
        ["Units:", "%s; areas in %s" % (u, AREA[u])],
        ["Origin:", "lower-left of the plan; +x = east (right), +y = north (up)"],
        ["Shape:", "%d corners, %s x %s %s overall" % (P["vertex_count"], num(plan["W"]), num(plan["H"]), u) if P else "no outline found"],
    ], gap=1).split("\n", 1)[1])

    out.append(section(1, "DIMENSIONS WRITTEN ON THE SKETCH"))
    rows = []
    for d in rd["dims"]:
        rows.append([d["id"], d["text"] or "-", num(d["value"]), {"h": "horizontal", "v": "vertical"}.get(d["orient"], "angled"),
                     num(d.get("solved")), "OK" if d.get("ok") else ("conflict" if "ok" in d else "not used"), dim_between(B, d)])
    out.append(table(["ID", "Written", "Value (%s)" % u, "Direction", "Solved", "Check", "Measures between"], rows) if rows
               else "No dimensions were read.")
    bad = [d for d in rd["dims"] if d.get("ok") is False]
    out.append("\n" + ("All dimensions agree with each other." if rd["dims"] and not bad else
                       "%d dimension(s) conflict; see NOTES AND CHECKS below." % len(bad) if bad else ""))

    if P:
        out.append(section(2, "CORNERS (VERTICES)"))
        rows = []
        for i, v in enumerate(P["vertices"]):
            rows.append([v["id"], num(v["xy"][0]), num(v["xy"][1]), num(v["xy"][0] * k), num(v["xy"][1] * k),
                         v["corner"], "(%d, %d)" % (round(rd["outline"][i][0]), round(rd["outline"][i][1]))])
        out.append(table(["ID", "x (%s)" % u, "y (%s)" % u, "x (%s)" % alt, "y (%s)" % alt, "Corner type", "Sketch position (px)"], rows))
        out.append("\nOutline order: " + " > ".join(v["id"] for v in P["vertices"]) + " > back to C1 (%s)" % P["winding"])

        out.append(section(3, "EDGES (LINES AS VECTORS)"))
        rows = []
        for e in P["edges"]:
            did, how = B["edge_src"].get(e["id"], (None, "-"))
            d = next((x for x in rd["dims"] if x["id"] == did), None)
            if d:
                src = "%s %s (%s)" % (did, d["text"] or num(d["value"]), how)
            elif did and "+" in did:
                parts = [x for x in rd["dims"] if x["id"] in did.split("+")]
                src = " + ".join("%s %s" % (x["id"], x["text"] or num(x["value"])) for x in parts) + " (chain)"
            else:
                src = how
            rows.append([e["id"], "%s > %s" % (e["start"], e["end"]), pt(e["start_xy"]), pt(e["end_xy"]), pt(e["vector"]),
                         pt(e["unit_vector"]), pt(e["outward_normal"]), num(e["length"]), num(e["length"] * k),
                         num(e["angle_deg"]) + "°", e["faces"], src])
        out.append(table(["ID", "From > To", "Start", "End", "Vector (dx, dy)", "Unit vector", "Outward normal",
                          "Length (%s)" % u, "Length (%s)" % alt, "Angle", "Faces", "Length set by"], rows))
        out.append("\nMidpoints: " + "  ".join("%s %s" % (e["id"], pt(e["midpoint"])) for e in P["edges"]))

        out.append(section(4, "FLOOR PLATE PROPERTIES"))
        bb, sm = P["bounding_box"], P["second_moment_about_centroid"]
        a_alt = P["area"] * k * k
        out.append(table(["", "", ""], [
            ["Floor area", "%s %s" % (num(P["area"], True), AREA[u]), "(%s %s)" % (num(a_alt, True, 1), AREA[alt])],
            ["Perimeter", "%s %s" % (num(P["perimeter"], True), u), "(%s %s)" % (num(P["perimeter"] * k, True, 2), alt)],
            ["Bounding box", "%s x %s %s" % (num(bb["width_x"]), num(bb["depth_y"]), u),
             "(x %s to %s, y %s to %s)" % (num(bb["min"][0]), num(bb["max"][0]), num(bb["min"][1]), num(bb["max"][1]))],
            ["Bounding box area", "%s %s" % (num(P["bounding_box_area"], True), AREA[u]), ""],
            ["Fill ratio", num(P["fill_ratio"], nd=4), "(floor area / bounding box area)"],
            ["Aspect ratio", "%s : 1" % num(P["aspect_ratio"]), ""],
            ["Centroid", "%s %s" % (pt(P["centroid"]), u), ""],
            ["Ixx about centroid", "%s %s4" % (num(sm["Ixx"], True), u), ""],
            ["Iyy about centroid", "%s %s4" % (num(sm["Iyy"], True), u), ""],
            ["Ixy about centroid", "%s %s4" % (num(sm["Ixy"], True), u), ""],
            ["Vertices / edges", "%d / %d" % (P["vertex_count"], P["edge_count"]), ""],
            ["Re-entrant corners", ", ".join(P["re_entrant_corners"]) or "none", ""],
            ["Gross area, all levels", "%s %s" % (num(P["area"] * lv["count"], True), AREA[u]), "(%d level%s)" % (lv["count"], "s" * (lv["count"] > 1))],
        ], gap=3).split("\n", 1)[1])

    out.append(section(5, "LEVELS"))
    out.append(table(["", ""], [
        ["Storeys", str(lv["count"])], ["Storey height", "%s %s" % (num(lv["storey_height"]), u)],
        ["Slab thickness", "%s %s" % (num(lv["slab_thickness"]), u)],
        ["Total height", "%s %s" % (num(lv["count"] * lv["storey_height"]), u)], ["Source", lv["source"]],
    ]).split("\n", 1)[1])

    if plan["columns"] or plan["grids_x"] or plan["grids_y"] or plan["cores"]:
        out.append(section(6, "GRID, COLUMNS AND CORES"))
        if plan["grids_x"]:
            out.append("Grid lines (x): " + "  ".join("%s at x = %s" % (g["label"], num(g["pos"])) for g in plan["grids_x"]))
        if plan["grids_y"]:
            out.append("Grid lines (y): " + "  ".join("%s at y = %s" % (g["label"], num(g["pos"])) for g in plan["grids_y"]))
        if plan["columns"]:
            out.append("Columns (%d): " % len(plan["columns"]) + "  ".join(pt(c) for c in plan["columns"]))
        for i, c in enumerate(plan["cores"]):
            out.append("Core %d: " % (i + 1) + " > ".join(pt(p) for p in c))
        n_notes = 7
    else:
        n_notes = 6

    out.append(section(n_notes, "NOTES AND CHECKS"))
    notes = []
    if rd["notes"]:
        notes.append("Text on the sketch: " + " | ".join(rd["notes"]))
    if not (plan["columns"] or plan["grids_x"] or plan["grids_y"] or plan["cores"]):
        notes.append("No column grid, columns or core were drawn.")
    rows = [[d["id"], d["text"] or num(d["value"]), "%d px" % round(d["span_px"]), "%s px/%s" % (num(d["span_px"] / d["value"], nd=2), u)]
            for d in rd["dims"] if d.get("value") and d.get("span_px")]
    if rows:
        ratios = [d["span_px"] / d["value"] for d in rd["dims"] if d.get("value") and d.get("span_px")]
        spread = max(ratios) / min(ratios)
        notes.append(("The sketch is not to scale (drawn scale varies %.1fx between dimensions). " % spread if spread > 1.15
                      else "The sketch is close to scale. ") +
                     "All coordinates come from the written dimensions, not the drawn proportions:\n" +
                     "\n".join("      " + l for l in table(["Dim", "Written", "Drawn", "Drawn scale"], rows).split("\n")))
    notes += plan["warnings"]
    notes += ["Reader: " + w for w in rd["warnings"]]
    notes.append("Corner pixel positions are a visual reading of the image and may be off by a few pixels; "
                 "this does not change the dimensioned coordinates.")
    out.append("\n".join("- " + n for n in notes))
    return "\n".join(out).replace("\n\n\n", "\n\n") + "\n"


# ---------------------------------------------------------------- structured data
def plan_json(B):
    u, plan, P, lv = B["units"], B["plan"], B["props"], B["levels"]
    srcs = B["edge_src"]
    edges = []
    for e in (P or {}).get("edges", []):
        did, how = srcs.get(e["id"], (None, ""))
        edges.append(dict(e, dimension=did, length_source=how))
    return {
        "schema": "sketch-plan/0.2",
        "source": {"image": os.path.basename(B["image_path"]), "image_size_px": list(B["image"].size),
                   "read": datetime.date.today().isoformat(), "model": B["raw"].get("_meta", {}).get("model")},
        "units": {"length": u, "area": AREA[u], "origin": "lower-left of the plan", "axes": "+x east (right), +y north (up)"},
        "levels": lv,
        "outline": plan["outline"],
        "floor": {k: v for k, v in (P or {}).items() if k not in ("edges", "vertices")},
        "vertices": [dict(v, sketch_px=[round(c) for c in B["rd"]["outline"][i]]) for i, v in enumerate((P or {}).get("vertices", []))],
        "edges": edges,
        "dimensions": [{"id": d["id"], "written": d["text"], "value": d["value"], "orientation": d["orient"],
                        "solved": d.get("solved"), "agrees": d.get("ok"), "measures": dim_between(B, d)} for d in B["rd"]["dims"]],
        "columns": plan["columns"], "cores": plan["cores"],
        "grids": {"x": plan["grids_x"], "y": plan["grids_y"]},
        "notes": B["rd"]["notes"], "warnings": plan["warnings"] + B["rd"]["warnings"],
    }


def write_csv(B, path):
    P = B["props"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["edge", "start", "end", "x1", "y1", "x2", "y2", "dx", "dy", "length", "angle_deg",
                    "orientation", "faces", "normal_x", "normal_y", "dimension", "length_source"])
        for e in (P or {}).get("edges", []):
            did, how = B["edge_src"].get(e["id"], ("", ""))
            w.writerow([e["id"], e["start"], e["end"], *e["start_xy"], *e["end_xy"], *e["vector"], e["length"],
                        e["angle_deg"], e["orientation"], e["faces"], *e["outward_normal"], did or "", how])


def dxf_text(B):
    P, plan, lv, u = B["props"], B["plan"], B["levels"], B["units"]
    L = []
    add = lambda *a: L.extend(str(v) for v in a)
    add(0, "SECTION", 2, "ENTITIES")
    if P:
        big = max(plan["W"], plan["H"]) or 1
        th = big * 0.016
        for e in P["edges"]:
            (x1, y1), (x2, y2) = e["start_xy"], e["end_xy"]
            add(0, "LINE", 8, "SLAB_EDGE", 10, x1, 20, y1, 30, 0, 11, x2, 21, y2, 31, 0)
            mx, my = e["midpoint"]
            nx, ny = e["outward_normal"]
            tx, ty = mx + nx * th * 1.8, my + ny * th * 1.8
            add(0, "TEXT", 8, "EDGE_LENGTH", 10, tx, 20, ty, 30, 0, 40, th, 1, "%s %s" % (e["id"], num(e["length"])),
                50, 90 if e["orientation"] == "vertical" else 0, 72, 1, 11, tx, 21, ty, 31, 0)
        for v in P["vertices"]:
            add(0, "TEXT", 8, "CORNERS", 10, v["xy"][0] + th * 0.4, 20, v["xy"][1] + th * 0.4, 30, 0, 40, th * 0.8, 1,
                "%s %s" % (v["id"], pt(v["xy"])))
        for i in range(1, lv["count"] + 1):
            for e in P["edges"]:
                (x1, y1), (x2, y2) = e["start_xy"], e["end_xy"]
                z = i * lv["storey_height"]
                add(0, "LINE", 8, "LEVELS", 10, x1, 20, y1, 30, z, 11, x2, 21, y2, 31, z)
        add(0, "POINT", 8, "CENTROID", 10, P["centroid"][0], 20, P["centroid"][1], 30, 0)
    for c in plan["columns"]:
        add(0, "CIRCLE", 8, "COLUMN", 10, c[0], 20, c[1], 30, 0, 40, COLUMN[u] / 2)
    for core in plan["cores"]:
        for i, a in enumerate(core):
            b = core[(i + 1) % len(core)]
            add(0, "LINE", 8, "CORE", 10, a[0], 20, a[1], 30, 0, 11, b[0], 21, b[1], 31, 0)
    ext = max(plan["W"], plan["H"]) * 0.08
    for g in plan["grids_x"]:
        add(0, "LINE", 8, "GRID", 10, g["pos"], 20, -ext, 30, 0, 11, g["pos"], 21, plan["H"] + ext, 31, 0)
    for g in plan["grids_y"]:
        add(0, "LINE", 8, "GRID", 10, -ext, 20, g["pos"], 30, 0, 11, plan["W"] + ext, 21, g["pos"], 31, 0)
    add(0, "ENDSEC", 0, "EOF")
    return "\n".join(L)


def svg_text(B):
    P, plan, u = B["props"], B["plan"], B["units"]
    if not P:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100"><text x="10" y="50">No outline found</text></svg>'
    bw, bh = max(plan["W"], 1e-9), max(plan["H"], 1e-9)
    k = 800 / max(bw, bh)
    m = 90
    W, H = bw * k + 2 * m, bh * k + 2 * m
    X = lambda x: m + x * k
    Y = lambda y: m + (bh - y) * k
    s = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %g %g" font-family="Helvetica, Arial, sans-serif">' % (W, H),
         '<rect width="%g" height="%g" fill="#ffffff"/>' % (W, H),
         '<polygon points="%s" fill="#dbe9f6" stroke="#1b2530" stroke-width="2"/>' % " ".join("%g,%g" % (X(x), Y(y)) for x, y in plan["outline"])]
    for c in plan["cores"]:
        s.append('<polygon points="%s" fill="#e3def3" stroke="#6b5ba8" stroke-width="1.5"/>' % " ".join("%g,%g" % (X(x), Y(y)) for x, y in c))
    for c in plan["columns"]:
        s.append('<rect x="%g" y="%g" width="6" height="6" fill="#1b2530"/>' % (X(c[0]) - 3, Y(c[1]) - 3))
    for e in P["edges"]:
        mx, my = e["midpoint"]
        nx, ny = e["outward_normal"]
        tx, ty = X(mx) + nx * 18, Y(my) - ny * 18
        rot = ' transform="rotate(-90 %g %g)"' % (tx, ty) if e["orientation"] == "vertical" else ""
        s.append('<text x="%g" y="%g" text-anchor="middle" dominant-baseline="middle" font-size="13" fill="#b35f0c"%s>%s · %s %s</text>'
                 % (tx, ty, rot, e["id"], num(e["length"]), u))
    for v in P["vertices"]:
        s.append('<circle cx="%g" cy="%g" r="3.5" fill="#1b2530"/>' % (X(v["xy"][0]), Y(v["xy"][1])))
        s.append('<text x="%g" y="%g" font-size="11" fill="#4f5d69">%s %s</text>' % (X(v["xy"][0]) + 6, Y(v["xy"][1]) - 6, v["id"], pt(v["xy"])))
    c = P["centroid"]
    s.append('<circle cx="%g" cy="%g" r="4" fill="none" stroke="#1d6cb0" stroke-width="1.5"/>' % (X(c[0]), Y(c[1])))
    s.append('<text x="%g" y="%g" font-size="11" fill="#1d6cb0">centroid %s</text>' % (X(c[0]) + 8, Y(c[1]) + 4, pt(c)))
    s.append('<text x="%g" y="%g" font-size="13" fill="#1b2530">Area %s %s · Perimeter %s %s · %s x %s %s</text>'
             % (m, H - 24, num(P["area"], True), AREA[u], num(P["perimeter"], True), u, num(bw), num(bh), u))
    s.append("</svg>")
    return "\n".join(s)


def overlay_png(B, path):
    """The photo with what was read drawn on top of it."""
    img = B["image"].copy()
    W, H = img.size
    d = ImageDraw.Draw(img, "RGBA")
    fs = max(14, round(max(W, H) / 70))
    try:
        font = ImageFont.truetype("arial.ttf", fs)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", fs)
        except OSError:
            font = ImageFont.load_default()
    lw = max(2, round(max(W, H) / 500))
    rd = B["rd"]
    ol = [tuple(p) for p in rd["outline"]]
    if len(ol) >= 3:
        d.polygon(ol, fill=(79, 163, 232, 55))
        d.line(ol + [ol[0]], fill=(79, 163, 232, 255), width=lw + 1)
        for i, p in enumerate(ol):
            r = lw * 3
            d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=(255, 255, 255, 255), outline=(79, 163, 232, 255), width=lw)
            d.text((p[0] + r * 1.5, p[1] + r * 1.2), "C%d" % (i + 1), fill=(79, 163, 232, 255), font=font)
    for c in rd["columns"]:
        r = lw * 2.5
        d.rectangle([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=(79, 163, 232, 255))
    for dm in rd["dims"]:
        a, b = tuple(dm["p1"]), tuple(dm["p2"])
        d.line([a, b], fill=(240, 160, 75, 255), width=lw)
        for p in (a, b):
            r = lw * 2
            d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=(240, 160, 75, 255))
        label = "%s %s = %s %s" % (dm["id"], dm["text"] or "", num(dm["value"]), B["units"])
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        bx = d.textbbox((0, 0), label, font=font)
        tw, th = bx[2] - bx[0], bx[3] - bx[1]
        x0, y0 = mx - tw / 2, my - th / 2
        d.rectangle([x0 - 6, y0 - 5, x0 + tw + 6, y0 + th + 7], fill=(240, 160, 75, 235))
        d.text((x0, y0 - bx[1] + 1), label, fill=(27, 37, 48, 255), font=font)
    img.save(path)


# ---------------------------------------------------------------- all outputs
def write_all(image_path, raw, out_dir, levels=None, storey_height=None):
    """Write every output for one photo into out_dir/<photo name>/. Returns the list of paths."""
    B = build(image_path, raw, levels, storey_height)
    stem = os.path.splitext(os.path.basename(image_path))[0]
    folder = os.path.join(out_dir, stem)
    os.makedirs(folder, exist_ok=True)
    p = lambda name: os.path.join(folder, "%s-%s" % (stem, name))
    paths = []

    def save_text(name, text):
        with open(p(name), "w", encoding="utf-8") as f:
            f.write(text)
        paths.append(p(name))

    save_text("summary.txt", summary_text(B))
    save_text("plan.json", json.dumps(plan_json(B), indent=2))
    save_text("reading.json", json.dumps(raw, indent=2))
    write_csv(B, p("edges.csv"))
    paths.append(p("edges.csv"))
    save_text("plan.dxf", dxf_text(B))
    save_text("plan.svg", svg_text(B))
    overlay_png(B, p("overlay.png"))
    paths.append(p("overlay.png"))
    return folder, paths, B
