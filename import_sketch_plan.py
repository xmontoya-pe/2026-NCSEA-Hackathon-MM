#! python3
"""
Import a sketch-plan JSON (from Sketch Plan Reader) into Rhino.

Run inside Rhino 7 or 8:
  Rhino 8: type ScriptEditor, open this file, press Run.
  Rhino 7: type RunPythonScript and pick this file.
Pick the .json file when asked. Save the result as a .3dm.

What it builds (all under the layer "Sketch Plan"):
  Slab Edge    closed outline curve at level 0
  Edges        one line per edge, named E1..En, with its data as Attribute User Text
  Corners      text dots C1..Cn with coordinates
  Dimensions   real Rhino aligned dimensions on every edge
  Slabs        a slab solid at every floor level plus the roof
  Massing      the full building volume (hidden by default)
  Columns / Cores / Grid   if the JSON has them
  Info         centroid point and a text block with floor area, perimeter, etc.
Floor properties are also stored as Document User Text (Panels > Document User Text).
"""
import json
import rhinoscriptsyntax as rs
import scriptcontext as sc
import Rhino

ROOT = "Sketch Plan"
UNIT_CODES = {"mm": 2, "m": 4, "ft": 9}


def load():
    path = rs.OpenFileName("Select the sketch-plan JSON", "JSON files (*.json)|*.json||")
    if not path:
        return None
    with open(path) as f:
        return json.load(f)


def layer(name, color, visible=True):
    full = ROOT + "::" + name
    if not rs.IsLayer(full):
        rs.AddLayer(name, color, visible, False, ROOT)
    rs.LayerVisible(full, visible)
    return full


def put(obj, lay, name=None):
    if obj:
        rs.ObjectLayer(obj, lay)
        if name:
            rs.ObjectName(obj, name)
    return obj


def fmt(v):
    return ("%.4f" % v).rstrip("0").rstrip(".")


def solid_from_outline(points, z0, height):
    """Closed planar outline extruded straight up from z0 by height, capped."""
    pts = [Rhino.Geometry.Point3d(p[0], p[1], z0) for p in points]
    pts.append(pts[0])
    crv = Rhino.Geometry.PolylineCurve(pts)
    srf = Rhino.Geometry.Surface.CreateExtrusion(crv, Rhino.Geometry.Vector3d(0, 0, height))
    brep = Rhino.Geometry.Brep.CreateFromSurface(srf)
    capped = brep.CapPlanarHoles(sc.doc.ModelAbsoluteTolerance) if brep else None
    return capped or brep


def main():
    data = load()
    if not data:
        return
    units = data["units"]["length"] if isinstance(data.get("units"), dict) else data.get("units", "m")
    if units in UNIT_CODES:
        rs.UnitSystem(UNIT_CODES[units], False, True)

    outline = data.get("outline") or [v["xy"] for v in data.get("vertices", [])]
    if len(outline) < 3:
        rs.MessageBox("The JSON has no floor outline.")
        return
    edges = data.get("edges", [])
    verts = data.get("vertices", [{"id": "C%d" % (i + 1), "xy": p} for i, p in enumerate(outline)])
    floor = data.get("floor", {})
    lv = data.get("levels", {})
    n = int(lv.get("count", 1) or 1)
    h = float(lv.get("storey_height", 3.6 if units == "m" else 12))
    t = float(lv.get("slab_thickness", 0.25 if units == "m" else 0.83))

    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    big = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0

    rs.EnableRedraw(False)
    if not rs.IsLayer(ROOT):
        rs.AddLayer(ROOT, (40, 40, 40))
    L_edge = layer("Slab Edge", (20, 30, 40))
    L_lines = layer("Edges", (29, 108, 176))
    L_corner = layer("Corners", (79, 93, 105))
    L_dim = layer("Dimensions", (179, 95, 12))
    L_slab = layer("Slabs", (150, 190, 225))
    L_mass = layer("Massing", (200, 210, 220), visible=False)
    L_info = layer("Info", (29, 108, 176))

    # dimension style scaled to the plan size
    ds_name = "SketchPlan"
    ds_index = sc.doc.DimStyles.FindName(ds_name)
    if ds_index is None:
        ds = sc.doc.DimStyles.Current.Duplicate()
        ds.Name = ds_name
        ds.DimensionScale = big / 60.0
        ds.LengthResolution = 2 if units != "mm" else 0
        sc.doc.DimStyles.Add(ds, False)

    # outline curve
    pts = [(p[0], p[1], 0) for p in outline] + [(outline[0][0], outline[0][1], 0)]
    crv = put(rs.AddPolyline(pts), L_edge, "Slab edge")
    for k, v in floor.items():
        if not isinstance(v, (dict, list)):
            rs.SetUserText(crv, k, str(v))

    # one line per edge, with its vector data as user text
    if not edges:
        for i, p in enumerate(outline):
            q = outline[(i + 1) % len(outline)]
            edges.append({"id": "E%d" % (i + 1), "start_xy": p, "end_xy": q})
    for e in edges:
        a, b = e["start_xy"], e["end_xy"]
        ln = put(rs.AddLine((a[0], a[1], 0), (b[0], b[1], 0)), L_lines, e["id"])
        for k in ("id", "start", "end", "start_xy", "end_xy", "vector", "unit_vector", "outward_normal",
                  "length", "angle_deg", "orientation", "faces", "midpoint", "dimension", "length_source"):
            if k in e:
                v = e[k]
                rs.SetUserText(ln, k, ", ".join(fmt(x) for x in v) if isinstance(v, list) else
                               (fmt(v) if isinstance(v, float) else str(v)))
        # aligned dimension, offset outward from the edge
        nrm = e.get("outward_normal")
        if not nrm:
            dx, dy = b[0] - a[0], b[1] - a[1]
            ln_len = (dx * dx + dy * dy) ** 0.5 or 1
            nrm = [dy / ln_len, -dx / ln_len]
        off = big * 0.06
        mid = ((a[0] + b[0]) / 2 + nrm[0] * off, (a[1] + b[1]) / 2 + nrm[1] * off, 0)
        dim = rs.AddAlignedDimension((a[0], a[1], 0), (b[0], b[1], 0), mid, ds_name)
        put(dim, L_dim, e["id"] + " dim")

    # corner dots
    for v in verts:
        x, y = v["xy"][0], v["xy"][1]
        label = "%s (%s, %s)" % (v["id"], fmt(x), fmt(y))
        dot = put(rs.AddTextDot(label, (x, y, 0)), L_corner, v["id"])
        if "corner" in v:
            rs.SetUserText(dot, "corner", v["corner"])

    # slabs at each floor level and the roof
    for i in range(n + 1):
        z = i * h
        brep = solid_from_outline(outline, z - t if i > 0 else 0, t)
        if brep:
            put(sc.doc.Objects.AddBrep(brep), L_slab, "Level %d" % i if i < n else "Roof")
    mass = solid_from_outline(outline, 0, n * h)
    if mass:
        put(sc.doc.Objects.AddBrep(mass), L_mass, "Massing")

    # columns, cores and grids when present
    cols = data.get("columns", [])
    if cols:
        L_col = layer("Columns", (30, 30, 30))
        cs = {"m": 0.4, "mm": 400, "ft": 1.33}.get(units, 0.4)
        for i in range(n):
            for c in cols:
                box = Rhino.Geometry.Box(Rhino.Geometry.Plane.WorldXY,
                                         Rhino.Geometry.Interval(c[0] - cs / 2, c[0] + cs / 2),
                                         Rhino.Geometry.Interval(c[1] - cs / 2, c[1] + cs / 2),
                                         Rhino.Geometry.Interval(i * h, (i + 1) * h - t))
                put(sc.doc.Objects.AddBox(box), L_col)
    cores = data.get("cores", [])
    if cores:
        L_core = layer("Cores", (107, 91, 168))
        for c in cores:
            brep = solid_from_outline(c, 0, n * h)
            if brep:
                put(sc.doc.Objects.AddBrep(brep), L_core, "Core")
    grids = data.get("grids", {})
    if grids.get("x") or grids.get("y"):
        L_grid = layer("Grid", (29, 108, 176))
        ext = big * 0.08
        for g in grids.get("x", []):
            put(rs.AddLine((g["pos"], min(ys) - ext, 0), (g["pos"], max(ys) + ext, 0)), L_grid, "Grid " + g["label"])
            put(rs.AddTextDot(g["label"], (g["pos"], max(ys) + ext, 0)), L_grid)
        for g in grids.get("y", []):
            put(rs.AddLine((min(xs) - ext, g["pos"], 0), (max(xs) + ext, g["pos"], 0)), L_grid, "Grid " + g["label"])
            put(rs.AddTextDot(g["label"], (max(xs) + ext, g["pos"], 0)), L_grid)

    # centroid, info text and document user text
    if floor.get("centroid"):
        c = floor["centroid"]
        put(rs.AddPoint((c[0], c[1], 0)), L_info, "Centroid")
        put(rs.AddTextDot("centroid", (c[0], c[1], 0)), L_info)
    area_u = {"ft": "ft2", "m": "m2", "mm": "mm2"}.get(units, "")
    src = data.get("source", "")
    if isinstance(src, dict):
        src = src.get("image", "")
    lines = ["Sketch plan: %s" % src]
    if "area" in floor:
        lines.append("Floor area: %s %s" % (fmt(floor["area"]), area_u))
    if "perimeter" in floor:
        lines.append("Perimeter: %s %s" % (fmt(floor["perimeter"]), units))
    bb = floor.get("bounding_box")
    if bb:
        lines.append("Bounding box: %s x %s %s" % (fmt(bb["width_x"]), fmt(bb["depth_y"]), units))
    lines.append("Levels: %d x %s %s" % (n, fmt(h), units))
    if floor.get("re_entrant_corners"):
        lines.append("Re-entrant corners: " + ", ".join(floor["re_entrant_corners"]))
    put(rs.AddText("\n".join(lines), (min(xs), min(ys) - big * 0.25, 0), big * 0.025), L_info, "Plan info")

    def flat(prefix, obj):
        for k, v in obj.items():
            key = prefix + k
            if isinstance(v, dict):
                flat(key + ".", v)
            elif isinstance(v, list):
                rs.SetDocumentUserText(key, ", ".join(str(x) for x in v))
            else:
                rs.SetDocumentUserText(key, str(v))
    flat("floor.", floor)
    rs.SetDocumentUserText("units", units)
    rs.SetDocumentUserText("levels.count", str(n))
    rs.SetDocumentUserText("levels.storey_height", fmt(h))

    rs.EnableRedraw(True)
    rs.ZoomExtents(all=True)
    print("Imported %d edges, %d levels. Edge data is in Properties > Attribute User Text." % (len(edges), n))


main()
