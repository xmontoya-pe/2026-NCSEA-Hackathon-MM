"""Seismic ELF engine: ASCE 7-22 equivalent lateral force with a rigid diaphragm, for steel braced
frames or concrete shear walls.

One engine for the two dashboards, which share modules 1 to 9. project["lateral_system"] picks one:

"braced_frame" (the default): "SEISMIC ELF - Rigid - BraceFrame - Dashboard-LR.html"
    modules 1-8   seismic weight, period, base shear, vertical distribution, diaphragm Fpx
    module 2      plan, load areas, frame stiffness, center of rigidity
    module 9      rigid-diaphragm distribution of one level's force to the frames
    module 12     braced frame forces by the story-shear method
    Not ported: collectors (10), chords (11), the flexible-diaphragm beam methods and the
    diaphragm beam V/M diagrams.

"shear_wall": "SEISMIC ELF - Rigid - Drift - ShearWall.html"
    modules 1-8   as above, with Cs calculated from R (Eq. 12.8-2 to 12.8-6)
    module 2      wall stiffness (point springs), cores, the cantilever wall model
    module 9      horizontal distribution: rigid diaphragm, simple spans or continuous beam, with V and M
    module 10     collectors
    module 11     chord forces
    module 12     shear wall forces by the story-shear method
    module 13     story drift (12.12)

Input is a project dict in the dashboard's saved-file format (what Save writes and Open reads, and
what the Bones drawing pad downloads). Method names follow the JavaScript so the two can be compared
side by side.
Units: ft, kips, psf; brace areas in in^2, wall thickness in in; stiffness in kip/in (braces and the
cantilever walls) or relative ft^4 (the wall point springs).

Uses only the standard library.
"""

import math
from copy import deepcopy

NAN = float("nan")
INF = float("inf")
EPS = 1e-6
E_STEEL = 29000.0  # ksi, same for every brace (cancels in the distribution)
KAPPA, E_G = 1.0, 2.5  # walls: shear shape factor; E/G for concrete (G = 0.4E)

# ASCE 7-22 Table 12.8-2 (approximate period parameters)
SYS = {"smrf": (0.028, 0.8), "cmrf": (0.016, 0.9), "ebf": (0.03, 0.75), "brbf": (0.03, 0.75), "other": (0.02, 0.75)}
# ASCE 7-22 Table 12.8-1 (coefficient for upper limit on period), linear interpolation between rows
CU_TABLE = [(0.1, 1.7), (0.15, 1.6), (0.2, 1.5), (0.3, 1.4), (0.4, 1.4)]
# ASCE 7-22 Table 1.5-2
IE_BY_RISK = {"I": 1.0, "II": 1.0, "III": 1.25, "IV": 1.5}
# ASCE 7-22 Table 12.12-1, allowable story drift / h_sx by Risk Category: "all other structures", and
# "4 stories or less with interior walls, partitions, ceilings and exterior walls designed for the drifts"
DRIFT_LIM = {"other": {"I": 0.020, "II": 0.020, "III": 0.015, "IV": 0.010},
             "low": {"I": 0.025, "II": 0.025, "III": 0.020, "IV": 0.015}}
ACC_LBL = {1: "+5%", -1: "-5%", 0: "none"}


# ---------- helpers that mirror JavaScript number handling ----------

def num(v):
    """The dashboard's num(): '' and None are NaN, anything else is Number(v)."""
    if v is None or v == "":
        return NAN
    try:
        return float(v)
    except (TypeError, ValueError):
        return NAN


def ok(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def rnd(v):
    """Math.round(v*1e6)/1e6 (JS rounds halves up, Python's round() does not)."""
    return math.floor(v * 1e6 + 0.5) / 1e6


def div(a, b):
    """a / b, NaN instead of ZeroDivisionError."""
    return a / b if b else NAN


def jpow(a, b):
    """Math.pow: NaN (not a complex number) for a negative base and a fractional exponent."""
    if not (ok(a) and ok(b)) or (a < 0 and b != int(b)):
        return NAN
    return a ** b


def js_str(v):
    """String(v) for a JS number: 10 -> '10', 10.5 -> '10.5' (used in the core joint keys)."""
    return str(int(v)) if ok(v) and v == int(v) else repr(v)


def truthy(v):
    """JavaScript truthiness of a number (NaN and 0 are false)."""
    return v is not None and v == v and v != 0


def clean(obj):
    """Make a result JSON-safe: non-finite floats become None, tuples become lists."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def zeros(a, b):
    return [[0.0] * b for _ in range(a)]


def solve_dense(A0, B0):
    """Gaussian elimination with partial pivoting; A m x m, B m x r (lists of rows). X, or None if singular."""
    m = len(A0)
    r = len(B0[0]) if m else 0
    A, B = [row[:] for row in A0], [row[:] for row in B0]
    big = max((abs(v) for row in A for v in row), default=0.0)
    for k in range(m):
        p = k
        for i in range(k + 1, m):
            if abs(A[i][k]) > abs(A[p][k]):
                p = i
        if not abs(A[p][k]) > 1e-11 * big:
            return None
        if p != k:
            A[p], A[k] = A[k], A[p]
            B[p], B[k] = B[k], B[p]
        ak, bk, d = A[k], B[k], A[k][k]
        for i in range(k + 1, m):
            f = A[i][k] / d
            if f == 0:
                continue
            ai, bi = A[i], B[i]
            for j in range(k, m):
                ai[j] -= f * ak[j]
            for j in range(r):
                bi[j] -= f * bk[j]
    X = zeros(m, r)
    for k in range(m - 1, -1, -1):
        for j in range(r):
            t = B[k][j]
            for i in range(k + 1, m):
                t -= A[k][i] * X[i][j]
            X[k][j] = t / A[k][k]
    return X


# ---------- code tables ----------

def cu_of(sd1):
    if not ok(sd1):
        return NAN
    if sd1 <= CU_TABLE[0][0]:
        return CU_TABLE[0][1]
    for (x0, y0), (x1, y1) in zip(CU_TABLE, CU_TABLE[1:]):
        if sd1 <= x1:
            return y0 + (y1 - y0) * (sd1 - x0) / (x1 - x0)
    return CU_TABLE[-1][1]


def k_from_t(t):
    """Distribution exponent, 12.8.3."""
    return 1.0 if t <= 0.5 else 2.0 if t >= 2.5 else 1 + (t - 0.5) / 2


def sdc_of(sds, sd1, risk):
    """Tables 11.6-1 and 11.6-2. Does not catch SDC E/F, which needs S1 >= 0.75."""
    def cat(v, limits):
        return next((i for i, lim in enumerate(limits) if v < lim), 3)

    i = max(cat(sds, (0.167, 0.33, 0.50)), cat(sd1, (0.067, 0.133, 0.20)))
    if risk == "IV" and i in (1, 2):
        i += 1
    return "ABCD"[i]


def cs_of(sds, sd1, t, ie, r):
    """Eq. 12.8-2, capped by Eq. 12.8-3 (T <= TL) and floored by Eq. 12.8-5.

    Eq. 12.8-6 (S1 >= 0.6) is skipped: that only happens in SDC D or higher.
    """
    cs = sds / (r / ie)
    cs_max = sd1 / (t * r / ie)
    cs_min = max(0.044 * sds * ie, 0.01)
    return max(min(cs, cs_max), cs_min)


def cs_calc(R, ie, sds, sd1, T, TL, S1):
    """The ShearWall file's csCalc(T), 12.8.1.1: Eq. 12.8-2, capped by 12.8-3 (T <= TL) or 12.8-4 (T > TL),
    floored by 12.8-5 and, where S1 >= 0.6g, 12.8-6. The lower limits win over the upper limit.
    Blank TL: Eq. 12.8-3 (T <= TL assumed). Blank S1: Eq. 12.8-6 is not checked."""
    why = ("Enter R in Building Inputs to calculate Cs." if not R > 0 else
           "Enter Ie in Building Inputs to calculate Cs." if not ie > 0 else
           "Enter SDS in Building Inputs to calculate Cs." if not sds >= 0 else "")
    if why:
        return {"ok": False, "why": why}
    Ri = R / ie
    eq2 = sds / Ri
    mx_eq = "12.8-4" if TL > 0 and T > TL else "12.8-3"
    mx = NAN
    if sd1 >= 0 and T > 0:
        mx = sd1 * TL / (T * T * Ri) if mx_eq == "12.8-4" else sd1 / (T * Ri)
    min5 = max(0.044 * sds * ie, 0.01)
    min6 = 0.5 * S1 / Ri if S1 >= 0.6 else NAN
    cs, gov = eq2, "12.8-2"
    if ok(mx) and cs > mx:
        cs, gov = mx, mx_eq
    if cs < min5:
        cs, gov = min5, "12.8-5"
    if ok(min6) and cs < min6:
        cs, gov = min6, "12.8-6"
    return {"ok": True, "why": "", "cs": cs, "gov": gov, "Ri": Ri, "eq2": eq2, "mx": mx, "mxEq": mx_eq, "min5": min5,
            "min6": min6, "T": T, "TL": TL, "S1": S1, "R": R, "ie": ie}


# ---------- project normalization (normalize / ensureLevels) ----------

def system_of(project):
    return "shear_wall" if (project or {}).get("lateral_system") == "shear_wall" else "braced_frame"


def _normalize_wall_inputs(st):
    """The ShearWall file's normalize(): wall stiffness inputs, R, Cd, Risk Category, the drift check settings."""
    pl = st["plan"]
    if not num(pl.get("tDef")) > 0:
        pl["tDef"] = 6
    if not isinstance(pl.get("kShear"), bool):
        pl["kShear"] = True
    if pl.get("kh") is None:
        pl["kh"] = ""
    if not 0 < num(pl.get("icr")) <= 1:
        pl["icr"] = 0.35
    if not num(pl.get("fc")) > 0:
        pl["fc"] = 5
    pl["jointsOff"] = [str(k) for k in pl.get("jointsOff") or []]
    if st.get("rc") not in IE_BY_RISK:
        st["rc"] = "II"
    for k, d in (("cd", 5), ("R", ""), ("S1", ""), ("TL", "")):
        if st.get(k) is None:
            st[k] = d
    if not isinstance(st.get("csAuto"), bool):
        st["csAuto"] = True
    dr = st.get("dr") or {}
    st["dr"] = {"type": "low" if dr.get("type") == "low" else "other", "at": "cm" if dr.get("at") == "cm" else "edge"}


def normalize(project):
    """Fill in a project the way the dashboard's normalize() and ensureLevels() do.

    Raises ValueError for inputs the engine can't handle (no levels, a non-orthogonal
    outline, diagonal or zero-length frames or walls).
    """
    st = deepcopy(project)
    levels = st.get("levels") or []
    if not levels:
        raise ValueError("The project needs at least one level.")
    st["levels"] = [{"name": str(L.get("name") or ""), "h": L.get("h"), "psf": L.get("psf"), "la": bool(L.get("la"))}
                    for L in levels]
    n = len(st["levels"])
    wall = system_of(st) == "shear_wall"
    st["lateral_system"] = "shear_wall" if wall else "braced_frame"

    if st.get("sys") != "custom" and st.get("sys") in SYS:
        st["ct"], st["xexp"] = SYS[st["sys"]]
    st["kAuto"] = bool(st.get("kAuto", True))

    pl = st["plan"] = dict(st.get("plan") or {})
    pl["verts"] = [[float(p[0]), float(p[1])] for p in pl.get("verts") or []]
    pl["closed"] = bool(pl.get("closed"))
    if not num(pl.get("aDef")) > 0:
        pl["aDef"] = 10
    if wall:
        _normalize_wall_inputs(st)
    if pl["closed"]:
        v = pl["verts"]
        for a, b in zip(v, v[1:] + v[:1]):
            if abs(a[0] - b[0]) >= EPS and abs(a[1] - b[1]) >= EPS:
                raise ValueError(f"Outline edge {a} to {b} is not parallel to X or Y. Only orthogonal plans are supported.")

    # load areas per level; older projects have one set for the whole building
    lv = list(pl.get("lv") or [])
    if isinstance(pl.get("la"), list) or isinstance(pl.get("laIds"), list):
        one = {"la": [r for r in pl.get("la") or [] if r and all(ok(num(r.get(c))) for c in ("x1", "y1", "x2", "y2"))],
               "laIds": pl.get("laIds") or [], "laActive": pl.get("laActive") or ""}
        if not lv:
            lv = [deepcopy(one) for _ in range(n)]
    if "useLA" in st:
        for L, src in zip(st["levels"], levels):
            if "la" not in src:
                L["la"] = bool(st["useLA"])
    lv = lv[:n]
    for j in range(n):
        item = deepcopy(lv[j - 1]) if j else {"la": [], "laIds": [], "laActive": ""}
        if j == len(lv):
            lv.append(item)
        elif not isinstance(lv[j], dict):
            lv[j] = item
    for o in lv:
        o["la"] = [{"id": str(r["id"]), **{c: float(r[c]) for c in ("x1", "y1", "x2", "y2")}}
                   for r in o.get("la") or []]
        o["laIds"] = [{"id": str(q["id"]), "psf": q.get("psf")} for q in o.get("laIds") or []]
    pl["lv"] = lv

    elem = "Wall" if wall else "Frame"
    frames = []
    for f in pl.get("lfrs") or []:
        l = dict(f)
        x1, x2 = sorted((float(l["x1"]), float(l["x2"])))
        y1, y2 = sorted((float(l["y1"]), float(l["y2"])))
        if abs(x2 - x1) >= EPS and abs(y2 - y1) >= EPS:
            raise ValueError(f"{elem} {l.get('label')} is not parallel to X or Y.")
        if abs(x2 - x1) < EPS and abs(y2 - y1) < EPS:
            raise ValueError(f"{elem} {l.get('label')} has zero length.")
        l.update(x1=x1, y1=y1, x2=x2, y2=y2, label=str(l.get("label", "")))
        if wall:
            # wall thickness per story (in); a missing story copies the one below, then t, then the default
            ts = list(l.get("ts") or [])[:n]
            for j in range(n):
                if j == len(ts):
                    ts.append(None)
                if not num(ts[j]) > 0:
                    ts[j] = ts[j - 1] if j and num(ts[j - 1]) > 0 else (num(l.get("t")) if num(l.get("t")) > 0
                                                                        else num(pl["tDef"]))
            l["ts"] = [num(t) for t in ts]
            frames.append(l)
            continue
        if not num(l.get("A")) > 0:
            l["A"] = pl["aDef"]
        l["up"] = l.get("up") if l.get("up") in ("a", "b") else "b"
        l["alt"] = bool(l.get("alt"))
        l["type"] = "x2" if l.get("type") == "x2" else "single"
        l["mid"] = "bot" if l.get("mid") == "bot" else "top"
        As = list(l.get("As") or [])[:n]
        for j in range(n):
            if j == len(As):
                As.append(None)
            if not num(As[j]) > 0:
                As[j] = As[j - 1] if j and num(As[j - 1]) > 0 else num(l["A"])
        l["As"] = As
        frames.append(l)
    pl["lfrs"] = frames
    return st


# ---------- the engine: modules shared by both systems ----------

class Engine:
    """Engine(project) returns a BracedFrameEngine or a ShearWallEngine, by project["lateral_system"]."""

    def __new__(cls, project):
        if cls is Engine:
            cls = ShearWallEngine if system_of(project) == "shear_wall" else BracedFrameEngine
        return super().__new__(cls)

    def __init__(self, project):
        self.S = normalize(project)
        self._st = self._poly_stats()
        self._la = {}
        self._R = None
        self._rp = {}

    # -- basics --
    @property
    def n(self):
        return len(self.S["levels"])

    @property
    def lfrs(self):
        return self.S["plan"]["lfrs"]

    def set_cs(self, cs):
        self.S["cs"] = cs
        self._R = None

    def label(self, i):
        name = self.S["levels"][i - 1]["name"].strip()
        return name or ("Roof" if i == self.n else f"Lvl {i}")

    # -- plan geometry --
    def _poly_stats(self):
        pl = self.S["plan"]
        v, n = pl["verts"], len(pl["verts"])
        if not pl["closed"] or n < 3:
            return None
        A = cx = cy = per = 0.0
        minx = miny = math.inf
        maxx = maxy = -math.inf
        for i in range(n):
            (x0, y0), (x1, y1) = v[i], v[(i + 1) % n]
            c = x0 * y1 - x1 * y0
            A += c
            cx += (x0 + x1) * c
            cy += (y0 + y1) * c
            per += math.hypot(x1 - x0, y1 - y0)
            minx, maxx, miny, maxy = min(minx, x0), max(maxx, x0), min(miny, y0), max(maxy, y0)
        A /= 2
        return {"A": abs(A), "sA": A, "cx": div(cx, 6 * A), "cy": div(cy, 6 * A), "per": per,
                "minx": minx, "maxx": maxx, "miny": miny, "maxy": maxy}

    def poly_stats(self):
        return self._st

    def plan_area(self):
        return self._st["A"] if self._st else NAN

    def cut_at(self, k, m):
        """Cut of the outline at station m along axis k (0 = x, 1 = y): intervals of the other coordinate."""
        v = self.S["plan"]["verts"]
        n, o, c = len(v), 1 - k, []
        for j in range(n):
            a, b = v[j], v[(j + 1) % n]
            if abs(a[o] - b[o]) < EPS and min(a[k], b[k]) < m < max(a[k], b[k]):
                c.append(a[o])
        c.sort()
        return [(c[j], c[j + 1]) for j in range(0, len(c) - 1, 2)]

    @staticmethod
    def la_r(r):
        return {"x1": min(r["x1"], r["x2"]), "x2": max(r["x1"], r["x2"]),
                "y1": min(r["y1"], r["y2"]), "y2": max(r["y1"], r["y2"])}

    def rect_in_plan(self, r):
        """Area of a rectangle inside the plan, and its centroid (slabs in x, each cut against the outline)."""
        q = self.la_r(r)
        xs = sorted({rnd(x) for x in [q["x1"], q["x2"]] +
                     [p[0] for p in self.S["plan"]["verts"] if q["x1"] < p[0] < q["x2"]]})
        A = Mx = My = 0.0
        for a, b in zip(xs, xs[1:]):
            w = b - a
            if w < EPS:
                continue
            for y0, y1 in self.cut_at(0, (a + b) / 2):
                lo, hi = max(y0, q["y1"]), min(y1, q["y2"])
                if hi > lo:
                    d = hi - lo
                    A += w * d
                    Mx += w * d * (a + b) / 2
                    My += w * d * (lo + hi) / 2
        return {"A": A, "cx": div(Mx, A), "cy": div(My, A)}

    # -- load areas --
    def LV(self, lv):
        lvs = self.S["plan"]["lv"]
        return lvs[lv - 1] if lv and 1 <= lv <= len(lvs) else {"la": [], "laIds": [], "laActive": ""}

    def la_psf(self, id_, lv):
        e = next((q for q in self.LV(lv)["laIds"] if q["id"] == id_), None)
        return num(e["psf"]) if e else NAN

    def la_overlap_a(self, p, q):
        a, b = self.la_r(p), self.la_r(q)
        x1, x2 = max(a["x1"], b["x1"]), min(a["x2"], b["x2"])
        y1, y2 = max(a["y1"], b["y1"]), min(a["y2"], b["y2"])
        return self.rect_in_plan({"x1": x1, "y1": y1, "x2": x2, "y2": y2})["A"] if (x2 - x1 > EPS and y2 - y1 > EPS) else 0

    def la_stats(self, lv):
        if lv in self._la:
            return self._la[lv]
        st, L = self._st, self.LV(lv)["la"]
        out = {"n": len(L), "ok": False, "err": "", "A": st["A"] if st else NAN, "cover": 0.0, "unc": NAN,
               "W": NAN, "cx": NAN, "cy": NAN, "byId": {}, "overlap": None}
        if not st:
            out["err"] = "Close the floor plan outline first."
        elif not L:
            out["err"] = "No load areas are drawn yet."
        else:
            W = Mx = My = 0.0
            bad = None
            for r in L:
                g, p = self.rect_in_plan(r), self.la_psf(r["id"], lv)
                out["cover"] += g["A"]
                b = out["byId"].setdefault(r["id"], {"A": 0.0, "W": 0.0, "n": 0})
                b["A"] += g["A"]
                b["n"] += 1
                if not p >= 0:
                    if bad is None:
                        bad = r["id"]
                else:
                    w = p * g["A"] / 1000
                    b["W"] += w
                    W += w
                    Mx += w * g["cx"]
                    My += w * g["cy"]
            for i in range(len(L)):
                if out["overlap"]:
                    break
                for j in range(i + 1, len(L)):
                    if self.la_overlap_a(L[i], L[j]) > 1e-6:
                        out["overlap"] = [i, j]
                        break
            out["W"], out["cx"], out["cy"] = W, div(Mx, W) if W > 0 else NAN, div(My, W) if W > 0 else NAN
            out["unc"] = out["A"] - out["cover"]
            tol = max(0.01, 1e-9 * out["A"])
            if out["overlap"]:
                i, j = out["overlap"]
                out["err"] = f"Load areas {L[i]['id']} and {L[j]['id']} overlap. Load areas cannot overlap."
            elif bad is not None:
                out["err"] = f"Load area ID {bad} has no psf."
            elif out["unc"] > tol:
                out["err"] = (f"{out['unc']:,.1f} ft² of the plan ({out['unc'] / out['A'] * 100:.2f}%) has no load area. "
                              "Load areas must cover the whole plan.")
            elif not W > 0:
                out["err"] = "The load areas weigh nothing (all psf are 0)."
            else:
                out["ok"] = True
        self._la[lv] = out
        return out

    def la_on(self, lv):
        """Load areas drive the weight and the CM only when the level's switch is on and the areas are valid."""
        return bool(lv and 1 <= lv <= self.n and self.S["levels"][lv - 1]["la"]) and self.la_stats(lv)["ok"]

    def cm_pt(self, st, lv):
        if self.la_on(lv):
            L = self.la_stats(lv)
            return [L["cx"], L["cy"]]
        return [st["cx"], st["cy"]] if st else [NAN, NAN]

    # -- modules 1-8: period, weight, base shear, vertical distribution, diaphragm forces --
    def period(self):
        S = self.S
        ct, x = num(S.get("ct")), num(S.get("xexp"))
        hn = num(S["levels"][-1]["h"])
        Ta = ct * jpow(hn, x)
        Cu = cu_of(num(S.get("sd1")))
        CuTa = Cu * Ta
        Tan = num(S.get("Tan"))
        T, note = Ta, "T = Ta (no analysis period entered)"
        if ok(Tan) and Tan > 0:
            if ok(CuTa) and Tan > CuTa:
                T, note = CuTa, f"Analysis T = {Tan:g} s capped at Cu·Ta"
            else:
                T, note = Tan, "Analysis T ≤ Cu·Ta, used as entered"
        return {"hn": hn, "Ta": Ta, "Cu": Cu, "CuTa": CuTa, "T": T, "note": note}

    def cs_value(self, P):
        """Cs used by compute(), and extra keys for its result. Braced frames: the typed Cs (analyze() fills a blank one)."""
        return num(self.S.get("cs")), {}

    def compute(self):
        if self._R is not None:
            return self._R
        S = self.S
        P = self.period()
        cs, extra = self.cs_value(P)
        k = (k_from_t(P["T"]) if ok(P["T"]) else NAN) if S["kAuto"] else num(S.get("k"))
        A = self.plan_area()
        rows = []
        for j, L in enumerate(S["levels"]):
            la = self.la_on(j + 1)
            p = self.la_stats(j + 1)["W"] * 1000 / A if la else num(L["psf"])
            rows.append({"i": j + 1, "name": self.label(j + 1), "h": num(L["h"]), "psf": p, "la": la, "w": A * p / 1000})
        W = sum(r["w"] for r in rows if ok(r["w"]))
        V = cs * W
        for r in rows:
            r["whk"] = r["w"] * jpow(r["h"], k)
        sumWhk = sum(r["whk"] for r in rows if ok(r["whk"]))
        for r in rows:
            r["cvx"] = r["whk"] / sumWhk if sumWhk > 0 else NAN
            r["F"] = r["cvx"] * V
        acc = 0.0
        for r in reversed(rows):
            acc += r["F"] if ok(r["F"]) else 0
            r["Vx"] = acc
        sumCvx = sum(r["cvx"] for r in rows if ok(r["cvx"]))
        sumF = sum(r["F"] for r in rows if ok(r["F"]))
        # diaphragm forces, 12.10.1.1
        sds, ie = num(S.get("sds")), num(S.get("ie"))
        sF = sW = 0.0
        for r in reversed(rows):
            sF += r["F"] if ok(r["F"]) else 0
            sW += r["w"] if ok(r["w"]) else 0
            r["sumFi"], r["sumWi"] = sF, sW
            r["ratio"] = sF / sW if sW > 0 else NAN
            r["wpx"] = r["w"]
            r["fpx1"] = r["ratio"] * r["wpx"]
            r["alpha"] = r["fpx1"] / r["F"] if ok(r["F"]) and r["F"] > 0 else NAN
            r["fmin"] = 0.2 * sds * ie * r["wpx"]
            r["fmax"] = 0.4 * sds * ie * r["wpx"]
            if ok(r["fpx1"]) and ok(r["fmin"]) and ok(r["fmax"]):
                r["gov"] = "min" if r["fpx1"] < r["fmin"] else "max" if r["fpx1"] > r["fmax"] else "eq"
                r["fpx"] = min(max(r["fpx1"], r["fmin"]), r["fmax"])
            else:
                r["gov"], r["fpx"] = "", NAN
        self._R = {"rows": rows, "W": W, "V": V, "cs": cs, "k": k, "sumWhk": sumWhk, "sumCvx": sumCvx,
                   "sumF": sumF, "P": P, "sds": sds, "ie": ie, **extra}
        return self._R

    def messages(self, R):
        """The dashboard's input messages for modules 1-8, as (section, text) pairs."""
        S, out = self.S, []
        if not ok(self.plan_area()):
            out.append(("weight", "Floor area comes from the typical floor plan. Draw and close the outline in section 2."))
        for j, L in enumerate(S["levels"]):
            if L["la"]:
                LS = self.la_stats(j + 1)
                if not LS["ok"]:
                    out.append(("weight", f"{self.label(j + 1)}, load areas (section 2): {LS['err']} "
                                          "Using the psf column for this level until this is fixed."))
        for j, L in enumerate(S["levels"]):
            nm = self.label(j + 1)
            for f in (["h"] if self.la_on(j + 1) else ["h", "psf"]):
                v = num(L[f])
                if not ok(v) or v < 0:
                    out.append(("weight", f"{nm}: {'height' if f == 'h' else 'seismic weight'} is missing or invalid."))
            if j == 0 and num(L["h"]) <= 0:
                out.append(("weight", f"{nm}: height must be greater than 0."))
            hp = num(S["levels"][j - 1]["h"]) if j else NAN
            if j > 0 and ok(num(L["h"])) and ok(hp) and num(L["h"]) <= hp:
                out.append(("weight", f"{nm} ({L['h']} ft) should be higher than {self.label(j)} ({S['levels'][j - 1]['h']} ft). "
                                      "Heights are cumulative from the base."))
        if not R["cs"] > 0:
            out.append(("base_shear", "Enter a positive Cs in Building Inputs."))
        if not ok(R["k"]):
            out.append(("base_shear", "k cannot be calculated until the period T is defined (see Fundamental Period)."
                        if S["kAuto"] else "Enter k."))
        elif R["k"] < 1 or R["k"] > 2:
            out.append(("base_shear", "Per 12.8.3, k ranges from 1 to 2."))
        if not num(S.get("ct")) > 0 or not num(S.get("xexp")) > 0:
            out.append(("period", "Enter positive Ct and x."))
        if not R["P"]["hn"] > 0:
            out.append(("period", "Roof height hn is not defined. Check the level heights in Seismic Weight."))
        if not ok(num(S.get("sd1"))):
            out.append(("period", "Enter SD1 in Building Inputs to determine Cu."))
        if not R["sds"] > 0:
            out.append(("diaphragm", "Enter SDS in Building Inputs for the Fpx limits."))
        if not R["ie"] > 0:
            out.append(("diaphragm", "Enter Ie in Building Inputs for the Fpx limits."))
        return out

    def _load_area_warnings(self):
        return [f"{self.label(j + 1)}: has load areas, but its load-area switch (la) is off, "
                "so the psf value is used for its weight and the plan centroid for its CM."
                for j, L in enumerate(self.S["levels"]) if not L["la"] and self.LV(j + 1)["la"]]

    # -- line geometry (frames or walls) --
    @staticmethod
    def l_dir(l):
        return "X" if abs(l["y1"] - l["y2"]) < EPS else "Y"

    def l_len(self, l):
        return l["x2"] - l["x1"] if self.l_dir(l) == "X" else l["y2"] - l["y1"]

    def story_h(self, i):
        L = self.S["levels"]
        if not 1 <= i <= len(L):
            return NAN
        return num(L[i - 1]["h"]) - (num(L[i - 2]["h"]) if i > 1 else 0)

    def wall_k(self, l, i):
        raise NotImplementedError

    # -- rigid diaphragm --
    def _bad_msg(self, P):
        raise NotImplementedError

    def _none_msg(self, dir_):
        raise NotImplementedError

    def _torsion_msg(self):
        raise NotImplementedError

    def rigid_props(self, story):
        if story in self._rp:
            return self._rp[story]
        i, L = story, self.lfrs
        ks = [self.wall_k(l, i) for l in L]
        ys = [j for j, l in enumerate(L) if self.l_dir(l) == "Y"]
        xs = [j for j, l in enumerate(L) if self.l_dir(l) == "X"]
        kY, kX = sum(ks[j] for j in ys), sum(ks[j] for j in xs)
        xR = sum(ks[j] * L[j]["x1"] for j in ys) / kY if kY > 0 else NAN
        yR = sum(ks[j] * L[j]["y1"] for j in xs) / kX if kX > 0 else NAN
        J = ((sum(ks[j] * (L[j]["x1"] - xR) ** 2 for j in ys) if ok(xR) else 0) +
             (sum(ks[j] * (L[j]["y1"] - yR) ** 2 for j in xs) if ok(yR) else 0))
        bad = any(not k > 0 for k in ks)
        self._rp[story] = out = {"st": self._st, "ys": ys, "xs": xs, "kY": kY, "kX": kX, "xR": xR, "yR": yR, "J": J,
                                 "bad": bad, "story": i, "ks": ks}
        return out

    def rigid_solve(self, dir_, F, acc, story, lv_cm=None):
        """Rigid diaphragm: translation + rotation about the CR of `story`. acc = +1 / -1 / 0 (5% accidental torsion).

        lv_cm: the level whose center of mass the force acts at; defaults to the level above the story.
        Frame (wall) results are keyed by index.
        """
        P = self.rigid_props(story)
        st = P["st"]
        if not st:
            return {"err": "Draw and close the floor plan outline in section 2 first."}
        if P["bad"]:
            return {"err": self._bad_msg(P)}
        kD = P["kY"] if dir_ == "Y" else P["kX"]
        if not kD > 0:
            return {"err": self._none_msg(dir_)}
        Lx, Ly = st["maxx"] - st["minx"], st["maxy"] - st["miny"]
        a = acc if ok(acc) else 0
        cm = self.cm_pt(st, lv_cm if lv_cm is not None else P["story"])
        dX = dY = 0.0
        xF, yF = cm
        if dir_ == "Y":
            e = a * 0.05 * Lx
            xF = cm[0] + e
            T = F * (xF - P["xR"])  # counterclockwise +
            dY = F / P["kY"]
        else:
            e = a * 0.05 * Ly
            yF = cm[1] + e
            T = -F * (yF - P["yR"])
            dX = F / P["kX"]
        th = 0.0
        if abs(T) > 1e-12 * max(1, abs(F)) * (Lx + Ly):
            if not P["J"] > 0:
                return {"err": self._torsion_msg()}
            th = T / P["J"]
        W = {}
        for j, l in enumerate(self.lfrs):
            k = P["ks"][j]
            if self.l_dir(l) == "Y":
                d, t = k * dY, k * th * (l["x1"] - P["xR"])
            else:
                d, t = k * dX, -k * th * (l["y1"] - P["yR"])
            W[j] = {"direct": d, "tors": t, "f": d + t, "k": k}
        return {"W": W, "P": P, "e": e, "T": T, "th": th, "xF": xF, "yF": yF, "cm": cm, "Lx": Lx, "Ly": Ly,
                "acc": a, "story": P["story"]}

    def line_groups(self, dir_):
        """Frames parallel to dir_, grouped by line (same x for Y frames, same y for X frames), sorted."""
        k, groups = (0 if dir_ == "Y" else 1), []
        for j, l in enumerate(self.lfrs):
            if self.l_dir(l) != dir_:
                continue
            s = rnd(l["x1"] if k == 0 else l["y1"])
            g = next((g for g in groups if abs(g["s"] - s) < EPS), None)
            if g is None:
                g = {"s": s, "lines": []}
                groups.append(g)
            g["lines"].append(j)
        groups.sort(key=lambda g: g["s"])
        for g in groups:
            g["L"] = sum(self.l_len(self.lfrs[j]) for j in g["lines"])
            g["name"] = " + ".join(self.lfrs[j]["label"] for j in g["lines"])
        return groups

    def _plan_ready(self):
        pl = self.S["plan"]
        return pl["closed"] and len(pl["verts"]) >= 4

    # -- the diaphragm as a beam on the support lines (modules 9 to 11) --
    def hd_geometry(self, dir_, lv=None):
        """Beam along the plan for load in dir_: depth segments, weight per foot with load areas, support lines."""
        pl = self.S["plan"]
        if not self._plan_ready():
            return {"err": "Draw and close the floor plan outline in section 2 first."}
        k, v = (0 if dir_ == "Y" else 1), pl["verts"]
        LA = self.la_on(lv)
        las = [{**self.la_r(r), "psf": self.la_psf(r["id"], lv)} for r in self.LV(lv)["la"]] if LA else []
        smin0, smax0 = min(p[k] for p in v), max(p[k] for p in v)
        cs = sorted({rnd(p[k]) for p in v} |
                    {rnd(x) for r in las for x in ((r["x1"], r["x2"]) if k == 0 else (r["y1"], r["y2"])) if smin0 < x < smax0})
        segs = []
        for a, b in zip(cs, cs[1:]):
            m = (a + b) / 2
            cut = self.cut_at(k, m)
            d = sum(hi - lo for lo, hi in cut)
            # weight per foot of beam (kips/ft): psf × length of each load area along the cut
            wm = NAN
            if LA:
                wm = 0.0
                for r in las:
                    r0, r1 = (r["x1"], r["x2"]) if k == 0 else (r["y1"], r["y2"])
                    q0, q1 = (r["y1"], r["y2"]) if k == 0 else (r["x1"], r["x2"])
                    if r0 < m < r1:
                        for lo, hi in cut:
                            Lc = min(hi, q1) - max(lo, q0)
                            if Lc > 0:
                                wm += r["psf"] * Lc / 1000
            last = segs[-1] if segs else None
            if last and abs(last["d"] - d) < EPS and (not LA or abs(last["m"] - wm) < 1e-12):
                last["s1"] = b
            else:
                segs.append({"s0": a, "s1": b, "d": rnd(d), "m": wm})
        return {"dir": dir_, "k": k, "segs": segs, "A": self._st["A"], "W": self.la_stats(lv)["W"] if LA else None,
                "lv": lv, "smin": cs[0], "smax": cs[-1], "groups": self.line_groups(dir_)}

    def solve_beam(self, G, F, method, acc, story):
        """Beam for total force F: reactions at the support lines (opposing the load, positive), and V / M along it.

        method: 'rigid' (wall forces from the rigid diaphragm), 'rigidT' (lines of G carry only the torsional forces
        of a load in the other direction), 'simple' (hinged at interior supports) or 'cont' (continuous beam).
        acc: accidental torsion for the rigid methods. story: the story whose stiffness distributes the force.
        """
        sup = [g["s"] for g in G["groups"]]
        nS = len(sup)
        if nS == 0:
            return {"err": f"No LFRS lines are parallel to {G['dir']}. Draw at least one {G['dir']} line in section 2."}
        RG, couples = None, []
        TORS = method == "rigidT"
        if TORS:
            RG = self.rigid_solve("Y" if G["dir"] == "X" else "X", F, acc, story, G["lv"])
            if "err" in RG:
                return {"err": RG["err"]}
        elif method == "rigid":
            RG = self.rigid_solve(G["dir"], F, acc, story, G["lv"])
            if "err" in RG:
                return {"err": RG["err"]}
            # moments applied to the diaphragm beam: perpendicular element forces (about the CM line) and accidental torsion
            ref = RG["cm"][1] if G["dir"] == "Y" else RG["cm"][0]
            for j, l in enumerate(self.lfrs):
                if self.l_dir(l) == G["dir"]:
                    continue
                w = RG["W"][j]
                c = w["f"] * (l["y1"] - ref) if G["dir"] == "Y" else w["f"] * (l["x1"] - ref)
                a, b = (l["x1"], l["x2"]) if G["dir"] == "Y" else (l["y1"], l["y2"])
                if abs(w["f"]) > 0:
                    couples.append({"a": rnd(max(a, G["smin"])), "b": rnd(min(b, G["smax"])), "c": c, "f": w["f"],
                                    "label": l["label"]})
        elif nS == 1:
            return {"err": f"Only one {G['dir']} support line ({G['groups'][0]['name']}). A flexible diaphragm needs at least "
                           "two lines in each direction; one line alone cannot resist the load without torsion."}
        bps = sorted({rnd(x) for x in [s["s0"] for s in G["segs"]] + [G["smax"]] + sup +
                      [p for c in couples for p in (c["a"], c["b"])]})
        ivs = []
        for a, b in zip(bps, bps[1:]):
            m = (a + b) / 2
            sg2 = next((x for x in G["segs"] if x["s0"] < m < x["s1"]), None)
            d = sg2["d"] if sg2 else 0
            # load: by plan depth (uniform psf), or by weight per foot when load areas are on (w = F·m/W)
            q = 0 if TORS else (F * (sg2["m"] if sg2 else 0) / G["W"] if truthy(G["W"]) else F * d / G["A"])
            ivs.append({"a": a, "b": b, "d": d, "q": q})
        R = [0.0] * nS
        if RG:
            for j, g in enumerate(G["groups"]):
                R[j] = sum(RG["W"][i]["f"] for i in g["lines"])
        elif method == "simple":
            # determinate: hinges at interior supports; end spans carry their cantilevers
            for p in range(nS - 1):
                a, b = sup[p], sup[p + 1]
                lo, hi = (-math.inf if p == 0 else a), (math.inf if p == nS - 2 else b)
                W = Ma = 0.0
                for iv in ivs:
                    u0, u1 = max(iv["a"], lo), min(iv["b"], hi)
                    if u1 <= u0:
                        continue
                    W += iv["q"] * (u1 - u0)
                    Ma += iv["q"] * ((u1 - a) ** 2 - (u0 - a) ** 2) / 2
                Rb = Ma / (b - a)
                R[p + 1] += Rb
                R[p] += W - Rb
        else:
            # continuous beam, direct stiffness method (Euler-Bernoulli elements, uniform EI, rigid supports)
            nn = len(bps)
            nd = 2 * nn
            K, P = zeros(nd, nd), [0.0] * nd
            for e, iv in enumerate(ivs):
                L = iv["b"] - iv["a"]
                L2, L3, q = L * L, L * L * L, iv["q"]
                ke = [[12, 6 * L, -12, 6 * L], [6 * L, 4 * L2, -6 * L, 2 * L2], [-12, -6 * L, 12, -6 * L], [6 * L, 2 * L2, -6 * L, 4 * L2]]
                fe = [q * L / 2, q * L2 / 12, q * L / 2, -q * L2 / 12]
                dof = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
                for r in range(4):
                    P[dof[r]] += fe[r]
                    for c in range(4):
                        K[dof[r]][dof[c]] += ke[r][c] / L3
            sup_node = [next((i for i, b in enumerate(bps) if abs(b - s) < EPS), -1) for s in sup]
            fixed = {2 * i for i in sup_node}
            free = [i for i in range(nd) if i not in fixed]
            m = len(free)
            A = [[K[r][c] for c in free] for r in free]
            bv = [P[r] for r in free]
            for c in range(m):
                piv = c
                for r in range(c + 1, m):
                    if abs(A[r][c]) > abs(A[piv][c]):
                        piv = r
                if abs(A[piv][c]) < 1e-14:
                    return {"err": "The support layout is unstable for a continuous beam."}
                A[c], A[piv] = A[piv], A[c]
                bv[c], bv[piv] = bv[piv], bv[c]
                for r in range(c + 1, m):
                    f = A[r][c] / A[c][c]
                    if not f:
                        continue
                    for j in range(c, m):
                        A[r][j] -= f * A[c][j]
                    bv[r] -= f * bv[c]
            x = [0.0] * m
            for r in range(m - 1, -1, -1):
                t = bv[r]
                for j in range(r + 1, m):
                    t -= A[r][j] * x[j]
                x[r] = t / A[r][r]
            D = [0.0] * nd
            for i, g in enumerate(free):
                D[g] = x[i]
            for j, ni in enumerate(sup_node):
                r = 2 * ni
                R[j] = P[r] - sum(K[r][c] * D[c] for c in range(nd))

        # shear and moment by statics from the reactions
        def r_at(s):
            return sum(R[j] for j, x in enumerate(sup) if abs(x - s) < EPS)

        span = bps[-1] - bps[0]
        pts, faces, ext, ist = [], {}, [], []
        cC = [c for c in couples if c["b"] - c["a"] < EPS]
        cD = [c for c in couples if c["b"] - c["a"] >= EPS]

        def c_at(s):
            return sum(c["c"] for c in cC if abs(c["a"] - s) < EPS)

        # accidental torsion = every mass element shifted by e, i.e. a distributed couple m(s) = e·w(s) (total F·e)
        acc_e = RG["e"] if (RG and not TORS) else 0

        def m_rate(iv):
            return sum(c["c"] / (c["b"] - c["a"]) for c in cD if iv["a"] >= c["a"] - EPS and iv["b"] <= c["b"] + EPS) + acc_e * iv["q"]

        # Perpendicular elements push along the beam axis. Their moments (couples c = f·(p − ref)) are integrated about the
        # center-of-mass line (ref), then the moment is taken about the mid-depth of the chords at each cut:
        #   M_mid(s) = M_ref(s) + N(s)·(ref − mid(s)),  N(s) = running sum of perpendicular forces = axial force across the cut
        def n_at(s):
            return sum(c["f"] for c in cC if abs(c["a"] - s) < EPS)

        def n_rate(iv):
            return sum(c["f"] / (c["b"] - c["a"]) for c in cD if iv["a"] >= c["a"] - EPS and iv["b"] <= c["b"] + EPS)

        ref_l = (RG["cm"][1] if G["dir"] == "Y" else RG["cm"][0]) if (RG and not TORS and couples) else NAN

        def d_mid(iv):
            if not ok(ref_l):
                return 0
            c = self.cut_at(G["k"], (iv["a"] + iv["b"]) / 2)
            return ref_l - (c[0][0] + c[-1][1]) / 2 if c else 0

        dMid = [d_mid(iv) for iv in ivs]
        V = M = Nf = 0.0  # M: about the CM line; Nf: axial force across the cut
        for i, s in enumerate(bps):
            DL = dMid[max(0, i - 1)] if ivs else 0
            DR = dMid[min(i, len(ivs) - 1)] if ivs else 0
            Vl = V
            pts.append({"s": s, "V": V, "M": M + Nf * DL, "N": Nf})
            V += r_at(s)
            M += c_at(s)
            Nf += n_at(s)
            pts.append({"s": s, "V": V, "M": M + Nf * DR, "N": Nf})
            faces[s] = {"Vl": Vl, "Vr": V, "dl": ivs[i - 1]["d"] if i > 0 else 0, "dr": ivs[i]["d"] if i < len(ivs) else 0}
            if i == len(bps) - 1:
                break
            iv = ivs[i]
            L = iv["b"] - iv["a"]
            n_seg = max(2, math.ceil(L / span * 240))
            mr, nr, D = m_rate(iv), n_rate(iv), dMid[i]
            mrc = mr + nr * D
            Ve, M0c = V + mrc, M + Nf * D  # moment about mid-depth within this interval
            ist.append({"a": iv["a"], "b": iv["b"], "V0": V, "M0": M0c, "q": iv["q"], "mr": mrc, "N0": Nf, "nr": nr})
            for t in range(1, n_seg + 1):
                xx = L * t / n_seg
                pts.append({"s": iv["a"] + xx, "V": V - iv["q"] * xx, "M": M0c + Ve * xx - iv["q"] * xx * xx / 2, "d": iv["d"],
                            "N": Nf + nr * xx})
            if iv["q"] > 0 and Ve > 0 and Ve - iv["q"] * L < 0:
                xx = Ve / iv["q"]
                ext.append({"s": iv["a"] + xx, "M": M0c + Ve * xx - iv["q"] * xx * xx / 2, "d": iv["d"]})
            M = M + (V + mr) * L - iv["q"] * L * L / 2
            V = V - iv["q"] * L
            Nf += nr * L
        # extremes
        Vmax, Mpos, Mneg, vmax, Tmax = 0.0, {"M": 0.0, "s": NAN, "d": 0}, {"M": 0.0, "s": NAN, "d": 0}, {"v": 0.0, "s": NAN}, 0.0
        for p in pts:
            Vmax = max(Vmax, abs(p["V"]))
        for p in pts + ext:
            if p["M"] > Mpos["M"]:
                Mpos = {"M": p["M"], "s": p["s"], "d": p.get("d")}
            if p["M"] < Mneg["M"]:
                Mneg = {"M": p["M"], "s": p["s"], "d": p.get("d")}
        for iv in ivs:
            if not iv["d"] > 0:
                continue
            f0, f1 = faces[iv["a"]], faces[iv["b"]]
            for vv, s in ((abs(f0["Vr"]), iv["a"]), (abs(f1["Vl"]), iv["b"])):
                u = vv / iv["d"] * 1000
                if u > vmax["v"]:
                    vmax = {"v": u, "s": s}
        # chord force: M / d using the depth where the moment acts (at a support, the smaller adjacent depth)
        for mm in (Mpos, Mneg):
            if ok(mm["s"]):
                d = self.chord_depth(G, mm["s"])["d"]
                if d > 0:
                    Tmax = max(Tmax, abs(mm["M"]) / d)
        sumR = sum(R)
        # elements on one line share the line reaction by their stiffness in this story (flexible models)
        wallF = []
        for j, g in enumerate(G["groups"]):
            if RG:
                wallF.append([RG["W"][i]["f"] for i in g["lines"]])
                continue
            ks = [self.wall_k(self.lfrs[i], story) for i in g["lines"]]
            sk = sum(ks)
            wallF.append([R[j] * k / sk if sk > 0 else R[j] / len(g["lines"]) for k in ks])
        return {"story": story, "ist": ist, "torsionOnly": TORS, "R": R, "wallF": wallF, "RG": RG, "couples": couples,
                "sup": sup, "ivs": ivs, "bps": bps, "pts": pts, "faces": faces, "Vmax": Vmax, "Mpos": Mpos, "Mneg": Mneg,
                "vmax": vmax, "Tmax": Tmax, "sumR": sumR, "resV": V, "resM": M, "resN": Nf}

    def chord_depth(self, G, s):
        """Chord depth at s: the outer edges of the cut (at a support, the smaller of the two sides)."""
        span = max(1, G["smax"] - G["smin"])
        eps, best = 1e-6 * span, None
        for t in (s - eps, s + eps):
            if t <= G["smin"] or t >= G["smax"]:
                continue
            c = self.cut_at(G["k"], t)
            if not c:
                continue
            lo, hi = c[0][0], c[-1][1]
            d = hi - lo
            if best is None or d < best["d"] - 1e-9:
                best = {"d": d, "lo": lo, "hi": hi, "ivs": c, "gaps": len(c) > 1}
        return best or {"d": 0, "lo": NAN, "hi": NAN, "ivs": [], "gaps": False}

    @staticmethod
    def _iv_at(B, s):
        st = B["ist"]
        return next((x for x in st if x["a"] - 1e-9 <= s < x["b"] - 1e-9), st[-1])

    def moment_at(self, B, s):
        """Exact moment at s from the beam's interval states (M0, V0, q, couple rate)."""
        if not B["ist"]:
            return NAN
        iv = self._iv_at(B, s)
        dx = s - iv["a"]
        return iv["M0"] + (iv["V0"] + iv["mr"]) * dx - iv["q"] * dx * dx / 2

    def axial_at(self, B, s):
        """Axial force across the cut at s (tension +, for force in +X / +Y): running sum of perpendicular forces."""
        if not B["ist"]:
            return 0
        iv = self._iv_at(B, s)
        return iv["N0"] + iv["nr"] * (s - iv["a"])

    def chord_scan(self, G, B):
        """Chord force M / d along the whole beam: dense samples, interior moment peaks, both sides of every break."""
        ss = {rnd(p["s"]) for p in B["pts"]}
        for iv in B["ist"]:
            if iv["q"] > 0:
                dx = (iv["V0"] + iv["mr"]) / iv["q"]
                if 0 < dx < iv["b"] - iv["a"]:
                    ss.add(iv["a"] + dx)
        out = []
        for s in sorted(x for x in ss if G["smin"] <= x <= G["smax"]):
            M, D = self.moment_at(B, s), self.chord_depth(G, s)
            out.append({"s": s, "M": M, "d": D["d"], "T": M / D["d"] if D["d"] > 0 else 0})
        mx = {"T": 0.0, "s": NAN, "M": NAN, "d": NAN}
        for p in out:
            if abs(p["T"]) > abs(mx["T"]):
                mx = p
        return {"pts": out, "max": mx}


# ---------- steel braced frames ----------

class BracedFrameEngine(Engine):
    def _bad_msg(self, P):
        return (f"Story {P['story']} has no height. Check the level heights in section 3."
                if not self.story_h(P["story"]) > 0 else
                "Every braced frame needs a brace area A (section 2) for the rigid analysis.")

    def _none_msg(self, dir_):
        return f"No braced frames are parallel to {dir_}. Draw at least one {dir_} frame in section 2."

    def _torsion_msg(self):
        return ("The frame layout cannot resist torsion (all frames on one line, with no perpendicular "
                "frames). Add frames so the rigid diaphragm is stable.")

    # -- braced frames: geometry and stiffness --
    @staticmethod
    def top_end(l, i):
        """End ('a' = start x1/y1, 'b' = end x2/y2) that the brace in story i rises to."""
        up = "a" if l["up"] == "a" else "b"
        return ("b" if up == "a" else "a") if (l["alt"] and i % 2 == 0) else up

    @staticmethod
    def is_x2(l):
        return l["type"] == "x2"

    def frame_cols(self, l):
        """Column offsets from the start of the frame."""
        L = self.l_len(l)
        if not self.is_x2(l):
            return [0.0, L]
        m = num(l.get("L1"))
        if not (0 < m < L):
            m = L / 2
        return [0.0, m, L]

    @staticmethod
    def lam_story(l, i):
        """2X frames: True when story i's braces meet at the middle column at the top (Λ)."""
        return (0 if l["mid"] == "bot" else 1) == i % 2

    def story_braces(self, l, i):
        """Braces in story i: from / to = column index at the bottom / top of the brace."""
        if not self.is_x2(l):
            return [{"bay": 0, "from": 0, "to": 1}] if self.top_end(l, i) == "b" else [{"bay": 0, "from": 1, "to": 0}]
        if self.lam_story(l, i):
            return [{"bay": 0, "from": 0, "to": 1}, {"bay": 1, "from": 2, "to": 1}]
        return [{"bay": 0, "from": 1, "to": 0}, {"bay": 1, "from": 1, "to": 2}]

    @staticmethod
    def brace_a(l, i):
        """Brace area of frame l in story i (the braces directly below level i)."""
        As, j = l.get("As"), (i or 1) - 1
        v = num(As[j]) if isinstance(As, list) and j < len(As) else NAN
        return v if v > 0 else num(l.get("A"))

    def frame_geo(self, l, i):
        """Frame geometry and lateral stiffness in story i. Each brace: k = A·E·cos²θ / Lbr (kip/in); braces in parallel."""
        cols, h, A = self.frame_cols(l), self.story_h(i), self.brace_a(l, i)
        br = []
        for b in self.story_braces(l, i):
            Lb = abs(cols[b["to"]] - cols[b["from"]])
            Lbr = math.hypot(Lb, h)
            c = div(Lb, Lbr)
            k = A * E_STEEL * c * c / (Lbr * 12) if (A > 0 and Lb > 0 and h > 0) else NAN
            br.append({**b, "Lb": Lb, "Lbr": Lbr, "cos": c, "sin": div(h, Lbr), "theta": math.degrees(math.atan2(h, Lb)),
                       "k": k, "up": 1 if cols[b["to"]] > cols[b["from"]] else -1})
        k = sum(b["k"] for b in br)
        for b in br:
            b["share"] = b["k"] / k if k > 0 else NAN
        return {"Lb": self.l_len(l), "h": h, "Lbr": br[0]["Lbr"], "cos": br[0]["cos"], "theta": br[0]["theta"], "A": A,
                "k": k, "top": self.top_end(l, i), "braces": br, "cols": cols, "x2": self.is_x2(l),
                "lam": self.lam_story(l, i) if self.is_x2(l) else None}

    def wall_k(self, l, i):
        return self.frame_geo(l, i)["k"]

    # -- module 9: horizontal distribution at one level (rigid diaphragm) --
    def hd_level(self, dir_, lv, force="Fx", acc=1):
        R0 = self.compute()
        row = R0["rows"][lv - 1]
        F = row["fpx"] if force == "Fpx" else row["F"]
        base = {"level": lv, "name": row["name"], "dir": dir_, "force": force, "F": F, "story_h": self.story_h(lv)}
        if not self._plan_ready():
            return {**base, "error": "Draw and close the floor plan outline in section 2 first."}
        groups = self.line_groups(dir_)
        if not groups:
            return {**base, "error": f"No LFRS lines are parallel to {dir_}. Draw at least one {dir_} line in section 2."}
        RG = self.rigid_solve(dir_, F, acc, lv, lv)
        if "err" in RG:
            return {**base, "error": RG["err"]}
        if not F > 0:
            return {**base, "error": "The force at this level is zero or undefined. Check sections 3 to 8."}
        Rp, Rm = self.rigid_solve(dir_, F, 1, lv, lv), self.rigid_solve(dir_, F, -1, lv, lv)
        P = RG["P"]
        kD = P["kY"] if dir_ == "Y" else P["kX"]
        st = self._st
        smin, smax = (st["minx"], st["maxx"]) if dir_ == "Y" else (st["miny"], st["maxy"])

        def envelope(j):
            if "err" in Rp or "err" in Rm:
                return None
            a, b = Rp["W"][j]["f"], Rm["W"][j]["f"]
            same = abs(abs(a) - abs(b)) < 1e-9 * max(1, abs(a))
            return {"force": a if abs(a) >= abs(b) else b,
                    "governs": None if same else ("+5%" if abs(a) >= abs(b) else "-5%")}

        lines, warnings, sumR = [], [], 0.0
        for g in groups:
            R = sum(RG["W"][j]["f"] for j in g["lines"])
            sumR += R
            if g["s"] < smin - EPS or g["s"] > smax + EPS:
                warnings.append(f"{g['name']} is outside the floor plan.")
            if R < 0:
                warnings.append(f"{g['name']} has a negative reaction ({R:.2f} k): torsion reverses the force on this line.")
            lines.append({"name": g["name"], "location": g["s"], "R": R, "frames": [
                {"label": self.lfrs[j]["label"], "Lb": self.l_len(self.lfrs[j]), "A": self.brace_a(self.lfrs[j], lv),
                 "k": RG["W"][j]["k"], "share": RG["W"][j]["k"] / kD, "direct": RG["W"][j]["direct"],
                 "torsional": RG["W"][j]["tors"], "force": RG["W"][j]["f"], "envelope": envelope(j)}
                for j in g["lines"]]})
        if abs(sumR - F) > 1e-6 * max(1, F):
            warnings.append("Equilibrium check failed: the line reactions do not add up to F.")
        perpendicular = [{"label": l["label"], "location": l["y1"] if dir_ == "Y" else l["x1"],
                          "torsional": RG["W"][j]["tors"], "envelope": envelope(j)}
                         for j, l in enumerate(self.lfrs) if self.l_dir(l) != dir_]
        return {**base, "accidental_torsion": ACC_LBL[RG["acc"]],
                "cm": RG["cm"], "cr": [P["xR"], P["yR"]], "e": RG["e"], "force_point": [RG["xF"], RG["yF"]],
                "T": RG["T"], "J": P["J"], "k_total": kD, "lines": lines, "perpendicular": perpendicular,
                "sum_R": sumR, "warnings": warnings}

    # -- module 12: braced frame forces --
    def frame_story_shears(self, idx, acc):
        """V_i = ΣFx at and above level i, each level's force acting at its own CM, distributed with story i's stiffness."""
        l = self.lfrs[idx]
        dir_ = self.l_dir(l)
        if not self._plan_ready():
            return {"err": "Draw and close the floor plan outline in section 2 first."}
        g = next(g for g in self.line_groups(dir_) if idx in g["lines"])
        rows = self.compute()["rows"]
        if any(not ok(r["F"]) or not ok(r["h"]) for r in rows):
            return {"err": "Story forces are not defined. Check sections 3 to 8."}
        out = {}
        for i in range(self.n, 0, -1):
            Vi = sum(r["F"] for r in rows if r["i"] >= i)
            V = 0.0
            for r in rows:
                if r["i"] >= i:
                    RG = self.rigid_solve(dir_, r["F"], acc, i, r["i"])
                    if "err" in RG:
                        return {"err": RG["err"]}
                    V += RG["W"][idx]["f"]
            out[i] = {"i": i, "Vstory": Vi, "V": V}
        return {"g": g, "rows": rows, "V": out}

    def ve_calc(self, idx, acc):
        SS = self.frame_story_shears(idx, acc)
        if "err" in SS:
            return SS
        l = self.lfrs[idx]
        n, Lb = len(SS["rows"]), self.l_len(l)
        zs = [0.0] + [r["h"] for r in SS["rows"]]
        cols = self.frame_cols(l)
        nc = len(cols)
        lev = []
        for i in range(1, n + 1):
            g = self.frame_geo(l, i)
            V = SS["V"][i]["V"]
            Vup = SS["V"][i + 1]["V"] if i < n else 0.0
            # the story shear splits between the braces of the story by stiffness; P tension +
            braces = [{**b, "V": V * b["share"], "P": b["up"] * V * b["share"] / b["cos"]} for b in g["braces"]]
            lev.append({"i": i, "name": self.label(i), "z": zs[i], "hs": zs[i] - zs[i - 1], "V": V, "f": V - Vup,
                        "Vstory": SS["V"][i]["Vstory"], "geo": g, "top": g["top"], "braces": braces, "P": braces[0]["P"]})

        def Mat(k):
            """Overturning moment at elevation zs[k] from the frame's level forces above it."""
            return sum(x["f"] * (x["z"] - zs[k]) for x in lev if x["i"] > k)

        # columns by joint equilibrium from the roof down (pinned joints, no gravity); lev[i] is story i+1
        Nabove = [0.0] * nc
        for i in range(n, 0, -1):
            x, N = lev[i - 1], Nabove[:]
            for b in x["braces"]:
                N[b["to"]] -= b["P"] * b["sin"]
            if i < n:
                for b in lev[i]["braces"]:
                    N[b["from"]] += b["P"] * b["sin"]
            x["N"] = N
            Nabove = N
        for x in lev:
            x["Mbot"], x["Mtop"] = Mat(x["i"] - 1), Mat(x["i"])
            x["eqX"] = x["V"] - sum(b["up"] * b["P"] * b["cos"] for b in x["braces"])
            x["eqY"] = sum(x["N"]) + sum(b["P"] * b["sin"] for b in x["braces"])
            x["eqM"] = (sum(v * cols[c] for c, v in enumerate(x["N"])) +
                        sum(b["P"] * b["sin"] * cols[b["from"]] for b in x["braces"]) + x["Mbot"])
        Msum = sum(x["V"] * x["hs"] for x in lev)

        def tol(v):
            return 1e-9 * max(1, abs(v))

        statics = all(abs(x["eqX"]) < tol(x["V"]) and
                      abs(x["eqY"]) < tol(max(abs(b["P"]) for b in x["braces"])) and
                      abs(x["eqM"]) < tol(x["Mbot"]) * max(1, Lb) for x in lev)
        Pmax = {"P": 0.0, "i": 1, "bay": 0, "geo": lev[0]["braces"][0], "name": lev[0]["name"]}
        for x in lev:
            for b in x["braces"]:
                if abs(b["P"]) > abs(Pmax["P"]):
                    Pmax = {"P": b["P"], "i": x["i"], "bay": b["bay"], "geo": b, "name": x["name"]}
        return {"idx": idx, "l": l, "dir": self.l_dir(l), "L": Lb, "x2": self.is_x2(l), "cols": cols, "lev": lev,
                "zs": zs, "H": zs[n], "Mbase": Mat(0), "Vbase": lev[0]["V"], "Msum": Msum, "statics": statics,
                "Pmax": Pmax, "line": SS["g"]["name"], "acc": acc}

    def ve_gov(self, idx, acc_sel="env"):
        """With 'env', solve +5% and -5% and keep the case with the larger base overturning moment (ties: +5%)."""
        if acc_sel == "env":
            Kp, Km = self.ve_calc(idx, 1), self.ve_calc(idx, -1)
            if "err" in Kp:
                return Kp
            if "err" in Km:
                return Km
            g = Km if abs(Km["Mbase"]) > abs(Kp["Mbase"]) + 1e-9 * max(1, abs(Kp["Mbase"])) else Kp
            return {**g, "env": True, "gov": "-5%" if g is Km else "+5%"}
        return self.ve_calc(idx, num(acc_sel))

    def frame_forces(self, idx, acc_sel="env"):
        """Module 12 result for one frame, shaped for output."""
        l = self.lfrs[idx]
        K = self.ve_gov(idx, acc_sel)
        if "err" in K:
            return {"label": l["label"], "dir": self.l_dir(l), "error": K["err"]}
        warnings = []
        if any(x["f"] < -1e-9 for x in K["lev"]):
            warnings.append("This frame has a negative force at some levels (torsion, or a change in story stiffness, "
                            "reverses it). Values are shown with their sign.")
        if K["x2"] and len(K["lev"]) % 2 == 1:
            top = K["lev"][-1]
            warnings.append(f"Odd number of stories: the top story ({top['name']}) is half an X "
                            f"({'Λ' if top['geo']['lam'] else 'V'} only).")
        col_names = ["start", "middle", "end"] if K["x2"] else ["start", "end"]
        Pm = K["Pmax"]
        return {
            "label": l["label"], "dir": K["dir"], "line": K["line"], "type": l["type"], "L": K["L"], "columns_at": K["cols"],
            "accidental_torsion": K.get("gov") if K.get("env") else ACC_LBL.get(K["acc"], "none"),
            "envelope": bool(K.get("env")),
            "base_shear": K["Vbase"], "base_moment": K["Mbase"], "sum_Vh": K["Msum"],
            "checks": {"statics": K["statics"],
                       "moment": abs(K["Msum"] - K["Mbase"]) <= 1e-9 * max(1, abs(K["Mbase"]))},
            "max_brace": {"P": Pm["P"], "story": Pm["i"], "bay": Pm["bay"] + 1, "level": Pm["name"],
                          "theta": Pm["geo"]["theta"], "Lbr": Pm["geo"]["Lbr"]},
            "column_names": col_names,
            "stories": [{
                "story": x["i"], "level": x["name"], "elevation": x["z"], "h": x["hs"], "A": x["geo"]["A"],
                "force_at_level": x["f"], "shear": x["V"], "story_shear_total": x["Vstory"],
                "overturning_at_floor_below": x["Mbot"],
                "braces": [{"bay": b["bay"] + 1, "theta": b["theta"], "Lbr": b["Lbr"], "Lb": b["Lb"], "k": b["k"],
                            "share": b["share"], "V": b["V"], "P": b["P"]} for b in x["braces"]],
                "column_axial": x["N"],
                "equilibrium": {"sum_Fh": x["eqX"], "sum_Fv": x["eqY"], "sum_M": x["eqM"]},
            } for x in K["lev"]],
            "warnings": warnings,
        }

    # -- stiffness table (module 2, frame stiffness) --
    def stiffness(self):
        out = []
        for i in range(1, self.n + 1):
            P = self.rigid_props(i)
            out.append({"story": i, "level": self.label(i), "h": self.story_h(i), "kX": P["kX"], "kY": P["kY"],
                        "cr": [P["xR"], P["yR"]], "J": P["J"],
                        "frames": [{"label": l["label"], "dir": self.l_dir(l), "A": self.brace_a(l, i), "k": P["ks"][j]}
                                   for j, l in enumerate(self.lfrs)]})
        return out

    def analyze(self, hd_force="Fx", hd_acc=1, ve_acc="env", R=3.0, **_):
        S, warnings = self.S, []

        # Cs is an input in the dashboard. If it's missing, compute it from SDS, SD1 and R.
        sds, sd1, ie = num(S.get("sds")), num(S.get("sd1")), num(S.get("ie"))
        sdc = sdc_of(sds, sd1, "IV" if ie >= 1.5 else "II") if ok(sds) and ok(sd1) else None
        cs_source = "input" if ok(num(S.get("cs"))) else None
        if cs_source is None and R and ok(sds) and ok(sd1) and ie > 0:
            T = self.period()["T"]
            if ok(T) and T > 0:
                self.set_cs(cs_of(sds, sd1, T, ie, R))
                cs_source = f"computed with R = {R:g}"
        if sdc == "D" and R == 3:
            warnings.append("SDC D: the R = 3 'not specifically detailed' steel system is not permitted in SDC D to F "
                            "(ASCE 7-22 Table 12.2-1).")

        R0 = self.compute()
        warnings += [text for _, text in self.messages(R0)]
        warnings += self._load_area_warnings()
        st = self.poly_stats()
        rows = R0["rows"]

        result = {
            "inputs": {"project": S.get("project", ""), "cs": R0["cs"], "cs_source": cs_source, "ie": R0["ie"],
                       "sds": R0["sds"], "sd1": sd1, "sdc": sdc, "R": R, "sys": S.get("sys"),
                       "ct": num(S.get("ct")), "x": num(S.get("xexp"))},
            "plan": {"area": self.plan_area(), "perimeter": st["per"] if st else NAN,
                     "centroid": [st["cx"], st["cy"]] if st else None,
                     "Lx": st["maxx"] - st["minx"] if st else NAN, "Ly": st["maxy"] - st["miny"] if st else NAN,
                     "frames": [{"label": l["label"], "dir": self.l_dir(l), "x1": l["x1"], "y1": l["y1"], "x2": l["x2"],
                                 "y2": l["y2"], "L": self.l_len(l), "type": l["type"], "A_by_story": l["As"]}
                                for l in self.lfrs]},
            "period": {**R0["P"], "k": R0["k"]},
            "base_shear": {"W": R0["W"], "cs": R0["cs"], "V": R0["V"], "sum_F": R0["sumF"], "sum_Cvx": R0["sumCvx"]},
            "levels": _levels_out(self, rows, st),
            "stiffness": self.stiffness(),
            "horizontal_distribution": {
                "force": hd_force, "accidental_torsion": ACC_LBL[hd_acc],
                "X": [self.hd_level("X", r["i"], hd_force, hd_acc) for r in reversed(rows)],
                "Y": [self.hd_level("Y", r["i"], hd_force, hd_acc) for r in reversed(rows)],
            },
            "frame_forces": {"accidental_torsion": ve_acc if ve_acc == "env" else ACC_LBL[ve_acc],
                             "frames": [self.frame_forces(j, ve_acc) for j in range(len(self.lfrs))]},
            "warnings": warnings,
        }
        return clean(result)


def _levels_out(eng, rows, st):
    """Modules 3 to 8 by level, the same for both systems."""
    return [{
        "level": r["i"], "name": r["name"], "h": r["h"], "weight_from": "load areas" if r["la"] else "psf",
        "psf": r["psf"], "w": r["w"], "cm": eng.cm_pt(st, r["i"]) if st else None,
        "whk": r["whk"], "Cvx": r["cvx"], "F": r["F"], "Vx": r["Vx"],
        "diaphragm": {"sum_F": r["sumFi"], "sum_w": r["sumWi"], "ratio": r["ratio"], "wpx": r["wpx"],
                      "Fpx_eq": r["fpx1"], "Fpx_min": r["fmin"], "Fpx_max": r["fmax"], "Fpx": r["fpx"],
                      "governs": {"eq": "12.10-1", "min": "min, 12.10-2", "max": "max, 12.10-3"}.get(r["gov"]),
                      "alpha": r["alpha"]},
    } for r in rows]


# ---------- concrete shear walls ----------

class ShearWallEngine(Engine):
    """Concrete shear walls: point springs for the distribution (modules 9 to 12), cantilever walls for drift (13).
    Walls joined at an end, at a T or end to end form a core: flexure on the full core section, shear on the
    segments parallel to the force, split by t·L."""

    def __init__(self, project):
        super().__init__(project)
        for i, l in enumerate(self.lfrs):
            l["_i"] = i
        self._core = None
        self._ws, self._cst, self._wm, self._fs, self._hg, self._sb, self._ss = {}, {}, {}, {}, {}, {}, {}
        self._dr = None

    def _bad_msg(self, P):
        if self.S["plan"]["kShear"] and not self.stiff_h() > 0:
            return "Enter the wall height for stiffness (section 2, Wall stiffness), or the level heights in section 3."
        return "Every shear wall needs a thickness at every level (section 2) for the rigid analysis."

    def _none_msg(self, dir_):
        return f"No shear walls are parallel to {dir_}. Draw at least one {dir_} wall in section 2."

    def _torsion_msg(self):
        return ("The wall layout cannot resist torsion (all walls on one line, with no perpendicular walls). "
                "Add walls so the rigid diaphragm is stable.")

    # -- inputs --
    def cs_value(self, P):
        S = self.S
        CS = cs_calc(num(S.get("R")), num(S.get("ie")), num(S.get("sds")), num(S.get("sd1")), P["T"], num(S.get("TL")),
                     num(S.get("S1")))
        calcd = bool(S["csAuto"]) and CS["ok"]
        return (CS["cs"] if calcd else num(S.get("cs"))), {"CS": CS, "csCalcd": calcd}

    @staticmethod
    def wall_t(l, i):
        """Thickness of wall l in story i, inches (the wall directly below level i)."""
        ts, j = l.get("ts"), (i or 1) - 1
        v = num(ts[j]) if isinstance(ts, list) and j < len(ts) else NAN
        return v if v > 0 else num(l.get("t"))

    @staticmethod
    def wall_t_txt(l):
        u = sorted({round(num(v), 2) for v in (l.get("ts") or [l.get("t")])})
        return js_str(u[0]) if len(u) == 1 else f"{js_str(u[0])} to {js_str(u[-1])}"

    def roof_h(self):
        return num(self.S["levels"][-1]["h"])

    def stiff_h(self):
        """Wall height for the point-spring stiffness: plan.kh, or the roof height when blank."""
        v = num(self.S["plan"].get("kh"))
        return v if v > 0 else self.roof_h()

    def icr(self):
        """Effective flexural stiffness, I_eff / I_g."""
        v = num(self.S["plan"].get("icr"))
        return v if 0 < v <= 1 else 0.35

    def Ec(self):
        """ksi: 57,000·√f'c (psi), normal-weight concrete."""
        fc = num(self.S["plan"].get("fc"))
        return 57 * math.sqrt(fc * 1000) if fc > 0 else NAN

    # -- point springs (cantilever, flexural and shear springs in series, relative, 3E/h³ factored out) --
    def solo_stiff(self, l, i):
        """A wall on its own, story i: k_f = (I_eff/I_g)·t·L³/12, k_v = t·L·h²/(3κ·E/G), k = 1/(1/k_f + 1/k_v), ft⁴."""
        t, L, c = self.wall_t(l, i) / 12, self.l_len(l), self.icr()
        if not t > 0 or not L > 0:
            return {"t": t * 12, "kf": NAN, "kv": NAN, "k": NAN, "beta": NAN}
        kf = c * t * L ** 3 / 12  # flexure: I_eff = (I_eff/I_g)·t·L³/12 (ft⁴)
        if self.S["plan"]["kShear"] is False:
            return {"t": t * 12, "kf": kf, "kv": INF, "k": kf, "beta": 1.0}
        h = self.stiff_h()
        if not h > 0:
            return {"t": t * 12, "kf": kf, "kv": NAN, "k": NAN, "beta": NAN}
        kv = t * L * h * h / (3 * KAPPA * E_G)  # shear: A·h²/(3κ·E/G) (ft⁴, same scale as I)
        k = 1 / (1 / kf + 1 / kv)  # springs in series = I_eff·β
        return {"t": t * 12, "kf": kf, "kv": kv, "k": k, "beta": k / kf}

    # -- cores: walls joined at a shared end, at a T or end to end act as one section --
    def core_topo(self):
        """Joints and cores. Two perpendicular walls are joined where their centerlines meet, if each reaches the other
        within half the other's thickest story (a corner, a T or a cross). Two walls on one line are joined where they
        touch or overlap (end to end). A joint in plan.jointsOff (its "x,y" key) is released."""
        if self._core is not None:
            return self._core
        L, off = self.lfrs, self.S["plan"]["jointsOff"]

        def ht(l):  # half the thickest story, ft
            vals = [num(v) for v in (l["ts"] if isinstance(l.get("ts"), list) else [l.get("t")])]
            return max([0.0] + [v for v in vals if ok(v)]) / 24

        J = {}

        def add_j(p, a, b, typ):
            key = f"{js_str(rnd(p[0]))},{js_str(rnd(p[1]))}"
            j = J.setdefault(key, {"key": key, "x": rnd(p[0]), "y": rnd(p[1]), "walls": [], "types": []})
            for w in (a, b):
                if w not in j["walls"]:
                    j["walls"].append(w)
            if typ not in j["types"]:
                j["types"].append(typ)

        for a in range(len(L)):
            for b in range(a + 1, len(L)):
                A, B = L[a], L[b]
                if not self.l_len(A) > 0 or not self.l_len(B) > 0:
                    continue
                if self.l_dir(A) != self.l_dir(B):
                    X, Y = (A, B) if self.l_dir(A) == "X" else (B, A)
                    x, y, hx, hy = Y["x1"], X["y1"], ht(X), ht(Y)
                    x1, x2 = min(X["x1"], X["x2"]), max(X["x1"], X["x2"])
                    y1, y2 = min(Y["y1"], Y["y2"]), max(Y["y1"], Y["y2"])
                    if x < x1 - hy - EPS or x > x2 + hy + EPS or y < y1 - hx - EPS or y > y2 + hx + EPS:
                        continue
                    end_x = x <= x1 + hy + EPS or x >= x2 - hy - EPS
                    end_y = y <= y1 + hx + EPS or y >= y2 - hx - EPS
                    add_j([x, y], a, b, "corner" if end_x and end_y else "T" if end_x or end_y else "cross")
                else:
                    on_x = self.l_dir(A) == "X"
                    if abs(A["y1"] - B["y1"]) >= EPS if on_x else abs(A["x1"] - B["x1"]) >= EPS:
                        continue
                    k0, k1 = ("x1", "x2") if on_x else ("y1", "y2")
                    a1, a2 = min(A[k0], A[k1]), max(A[k0], A[k1])
                    b1, b2 = min(B[k0], B[k1]), max(B[k0], B[k1])
                    s = max(a1, b1)
                    if s <= min(a2, b2) + EPS:
                        add_j([s, A["y1"]] if on_x else [A["x1"], s], a, b, "end to end")
        joints = [{"key": j["key"], "x": j["x"], "y": j["y"], "walls": sorted(j["walls"]), "on": j["key"] not in off,
                   "type": " / ".join(j["types"])} for j in J.values()]
        par = list(range(len(L)))

        def find(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i

        for j in joints:
            if j["on"]:
                for w in j["walls"][1:]:
                    r1, r2 = find(j["walls"][0]), find(w)
                    if r1 != r2:
                        par[r1] = r2
        grp = {}
        for i in range(len(L)):
            grp.setdefault(find(i), []).append(i)
        cores = [{"name": f"C{k + 1}", "walls": g}
                 for k, g in enumerate(sorted((g for g in grp.values() if len(g) > 1), key=lambda g: g[0]))]
        of = {i: c for c in cores for i in c["walls"]}
        for j in joints:
            j["core"] = of.get(j["walls"][0]) if j["on"] else None
        self._core = {"joints": joints, "cores": cores, "of": of}
        return self._core

    def core_of(self, idx):
        return self.core_topo()["of"].get(idx)

    def core_sec(self, c, i):
        """Composite section of core c in story i (thin walls on centerlines, ft): area, centroid, I about the centroidal
        axis parallel to Y (X load) and to X (Y load), and the shear areas of the segments parallel to X and to Y."""
        A = Sx = Sy = 0.0
        segs = []
        for idx in c["walls"]:
            l = self.lfrs[idx]
            t, L, on_x = self.wall_t(l, i) / 12, self.l_len(l), self.l_dir(l) == "X"
            cx, cy = (l["x1"] + l["x2"]) / 2, (l["y1"] + l["y2"]) / 2
            a = t * L
            A += a
            Sx += a * cx
            Sy += a * cy
            segs.append({"t": t, "L": L, "X": on_x, "cx": cx, "cy": cy, "a": a})
        if not A > 0 or any(not s["a"] > 0 for s in segs):
            return None
        xc, yc = Sx / A, Sy / A
        Iy = Ix = AvX = AvY = 0.0
        for s in segs:
            t, L = s["t"], s["L"]
            Iy += (t * L ** 3 / 12 if s["X"] else L * t ** 3 / 12) + s["a"] * (s["cx"] - xc) ** 2
            Ix += (L * t ** 3 / 12 if s["X"] else t * L ** 3 / 12) + s["a"] * (s["cy"] - yc) ** 2
            if s["X"]:
                AvX += s["a"]
            else:
                AvY += s["a"]
        return {"A": A, "xc": xc, "yc": yc, "Iy": Iy, "Ix": Ix, "AvX": AvX, "AvY": AvY}

    def core_stiff(self, c, i, d):
        """Core c, story i, load in direction d: k_f = (I_eff/I_g)·I_core, k_v = A_v·h²/(3κ·E/G), A_v of the parallel segments."""
        key = (c["name"], i, d)
        if key in self._cst:
            return self._cst[key]
        s = self.core_sec(c, i)
        out = None
        if s:
            I, Av = (s["Iy"], s["AvX"]) if d == "X" else (s["Ix"], s["AvY"])
            kf = self.icr() * I
            h = self.stiff_h()
            if not Av > 0:
                out = {"I": I, "Av": Av, "kf": kf, "kv": NAN, "k": NAN, "beta": NAN, "s": s}
            elif self.S["plan"]["kShear"] is False:
                out = {"I": I, "Av": Av, "kf": kf, "kv": INF, "k": kf, "beta": 1.0, "s": s}
            elif not h > 0:
                out = {"I": I, "Av": Av, "kf": kf, "kv": NAN, "k": NAN, "beta": NAN, "s": s}
            else:
                kv = Av * h * h / (3 * KAPPA * E_G)
                k = 1 / (1 / kf + 1 / kv)
                out = {"I": I, "Av": Av, "kf": kf, "kv": kv, "k": k, "beta": k / kf, "s": s}
        self._cst[key] = out
        return out

    def wall_stiff(self, idx, i):
        """Wall idx in story i: on its own, or its t·L share of its core's stiffness in its own direction."""
        key = (idx, i)
        if key in self._ws:
            return self._ws[key]
        l, c = self.lfrs[idx], self.core_of(idx)
        if not c:
            out = self.solo_stiff(l, i)
        else:
            t, K = self.wall_t(l, i), self.core_stiff(c, i, self.l_dir(l))
            if not K or not t > 0 or not K["Av"] > 0:
                out = {"t": t, "kf": NAN, "kv": NAN, "k": NAN, "beta": NAN, "core": c}
            else:
                share = (t / 12) * self.l_len(l) / K["Av"]
                out = {"t": t, "kf": share * K["kf"], "kv": share * K["kv"], "k": share * K["k"], "beta": K["beta"],
                       "core": c, "share": share, "K": K}
        self._ws[key] = out
        return out

    def wall_k(self, l, i):
        """Relative stiffness used everywhere (ft⁴)."""
        return self.wall_stiff(l["_i"], i)["k"]

    # -- cantilever wall model --
    def wall_model(self, idx, springs):
        """Wall lateral stiffness matrix K_L at the floor levels. springs = True: the point springs of modules 9 to 12 as
        a story-spring matrix (relative, ft⁴, forces only). Otherwise a cantilever fixed at a rigid base, with flexure
        (E_c·I_eff) and shear (G·A, κ) in every story at that story's thickness: f_ab = Σ ∫(z_a − s)(z_b − s)/(E·I) ds
        + ∫κ/(G·A) ds over the stories below min(a, b), inverted (kip/in). In a core, the full core's I and the shear area
        of its parallel segments, times this wall's t·L share averaged over the height."""
        key = (idx, springs)
        if key not in self._wm:
            self._wm[key] = self._wall_model(idx, springs)
        return self._wm[key]

    def _wall_model(self, idx, springs):
        l, n = self.lfrs[idx], self.n
        H = [0.0] + [num(L["h"]) for L in self.S["levels"]]
        for j in range(1, n + 1):
            if not H[j] - H[j - 1] > 0:
                return {"err": f"Story {j} has no height. Check the level heights in section 3."}
        if not self.l_len(l) > 0:
            return {"err": f"{l['label']}: the wall has no length."}
        for j in range(1, n + 1):
            if not self.wall_t(l, j) > 0:
                return {"err": f"{l['label']}: no wall thickness at {self.label(j)}."}
        if springs:
            KL = zeros(n, n)
            for j in range(1, n + 1):
                k = self.wall_k(l, j)
                if not k > 0:
                    return {"err": f"{l['label']}: no stiffness at {self.label(j)} (check the wall height for stiffness)."}
                KL[j - 1][j - 1] += k
                if j > 1:
                    KL[j - 2][j - 2] += k
                    KL[j - 1][j - 2] -= k
                    KL[j - 2][j - 1] -= k
            return {"KL": KL, "n": n}
        E = self.Ec()
        if not E > 0:
            return {"err": "Enter the concrete strength f′c (section 2, Wall stiffness)."}
        G, c, Lin, sh = E / E_G, self.icr(), self.l_len(l) * 12, self.S["plan"]["kShear"] is not False
        z = [h * 12 for h in H]
        core, d, secs = self.core_of(idx), self.l_dir(l), [None]
        for k in range(1, n + 1):
            if not core:
                t = self.wall_t(l, k)
                secs.append({"I": c * t * Lin ** 3 / 12, "A": t * Lin, "share": 1.0})
                continue
            s = self.core_sec(core, k)
            if not s:
                return {"err": f"{core['name']}: a wall has no thickness at {self.label(k)}."}
            Av = s["AvX"] if d == "X" else s["AvY"]
            secs.append({"I": c * (s["Iy"] if d == "X" else s["Ix"]) * 20736, "A": Av * 144,  # ft⁴ → in⁴, ft² → in²
                         "share": (self.wall_t(l, k) / 12) * self.l_len(l) / Av})
        share = sum(s["share"] for s in secs[1:]) / n
        D = zeros(n, n)
        for a in range(1, n + 1):
            for b in range(a, n + 1):
                za, zb = z[a], z[b]

                def P(s):  # ∫(z_a − s)(z_b − s) ds
                    return za * zb * s - (za + zb) * s * s / 2 + s * s * s / 3

                f = 0.0
                for k in range(1, a + 1):
                    I, A = secs[k]["I"], secs[k]["A"]
                    f += (P(z[k]) - P(z[k - 1])) / (E * I) + (KAPPA * (z[k] - z[k - 1]) / (G * A) if sh else 0)
                D[a - 1][b - 1] = D[b - 1][a - 1] = f
        KL = solve_dense(D, [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)])
        if KL is None:
            return {"err": f"{l['label']}: the wall flexibility could not be inverted."}
        for a in range(n):
            for b in range(a + 1):
                v = share * (KL[a][b] + KL[b][a]) / 2
                KL[a][b] = KL[b][a] = v
        return {"KL": KL, "D": D, "n": n, "share": share, "core": core}

    def full_solve(self, dir_, acc, springs):
        """Building model: 3 unknowns per level (u_x, u_y, θ about the plan centroid), K = Σ Tᵀ·K_L·T over the walls, one
        solve. Loads: Fx at each level's own CM, moved ±5% of the plan dimension for accidental torsion. θ is carried in
        in/ft so displacements are in inches with plan coordinates in feet."""
        key = (dir_, num(acc), springs)
        if key not in self._fs:
            self._fs[key] = self._full_solve(dir_, acc, springs)
        return self._fs[key]

    def _full_solve(self, dir_, acc, springs):
        st = self._st if self.S["plan"]["closed"] else None
        if not st:
            return {"err": "Draw and close the floor plan outline in section 2 first."}
        L, n = self.lfrs, self.n
        a = num(acc) if ok(num(acc)) else 0
        if not L:
            return {"err": "Draw shear walls in section 2 first."}
        if not any(self.l_dir(l) == dir_ for l in L):
            return {"err": self._none_msg(dir_)}
        Fs = [r["F"] for r in self.compute()["rows"]]
        if not all(ok(f) for f in Fs):
            return {"err": "The story forces are not available yet (check sections 1 to 6)."}
        x0, y0, N = st["cx"], st["cy"], 3 * n
        Kb, Lx, Ly = zeros(N, N), st["maxx"] - st["minx"], st["maxy"] - st["miny"]
        FR = []
        for idx, l in enumerate(L):
            M = self.wall_model(idx, springs)
            if "err" in M:
                return {"err": M["err"]}
            on_x = self.l_dir(l) == "X"
            arm = -(l["y1"] - y0) if on_x else (l["x1"] - x0)
            T = [[(3 * j + (0 if on_x else 1), 1.0), (3 * j + 2, arm)] for j in range(n)]
            for p in range(n):
                for q in range(n):
                    k = M["KL"][p][q]
                    if not k:
                        continue
                    for i, ci in T[p]:
                        for jj, cj in T[q]:
                            Kb[i][jj] += ci * k * cj
            FR.append((idx, M, T))
        P, pts = [0.0] * N, []
        for j in range(n):
            cm, F = self.cm_pt(st, j + 1), Fs[j]
            if dir_ == "Y":
                xF = cm[0] + a * 0.05 * Lx
                P[3 * j + 1] += F
                P[3 * j + 2] += F * (xF - x0)
                pts.append([xF, cm[1]])
            else:
                yF = cm[1] + a * 0.05 * Ly
                P[3 * j] += F
                P[3 * j + 2] += -F * (yF - y0)
                pts.append([cm[0], yF])
        # unknowns with no stiffness (for example u_x when only Y walls exist) carry no load and are left at zero
        big = max(abs(Kb[i][i]) for i in range(N))
        act = [i for i in range(N) if Kb[i][i] > 1e-12 * big]
        act_set = set(act)
        if len(act) < N and any(abs(P[i]) > 1e-12 for i in range(N) if i not in act_set):
            return {"err": "The wall layout cannot resist this loading (it needs walls in both directions, or walls on "
                           "more than one line, to resist torsion)."}
        sol = solve_dense([[Kb[i][j] for j in act] for i in act], [[P[i]] for i in act])
        if sol is None:
            return {"err": self._torsion_msg()}
        d = [0.0] * N
        for k, i in enumerate(act):
            d[i] = sol[k][0]

        def at(j, x, y):  # displacement in the load direction at (x, y), level j+1
            if j < 0:
                return 0.0
            return d[3 * j + 1] + d[3 * j + 2] * (x - x0) if dir_ == "Y" else d[3 * j] - d[3 * j + 2] * (y - y0)

        W = {}
        for idx, M, T in FR:
            u = [sum(c * d[i] for i, c in T[j]) for j in range(n)]
            f = [sum(k * u[b] for b, k in enumerate(row)) for row in M["KL"]]
            V = [0.0] * (n + 2)
            for i in range(n, 0, -1):
                V[i] = V[i + 1] + f[i - 1]
            drift, keff = [NAN], [NAN]
            for i in range(1, n + 1):
                drift.append(u[i - 1] - (u[i - 2] if i > 1 else 0))
                keff.append(V[i] / drift[i] if abs(drift[i]) > 1e-14 else NAN)
            W[idx] = {"u": u, "f": f, "V": V, "drift": drift, "keff": keff}
        story = [None]
        for i in range(1, n + 1):
            cm = self.cm_pt(st, i)
            e1, e2 = ([st["minx"], cm[1]], [st["maxx"], cm[1]]) if dir_ == "Y" else ([cm[0], st["miny"]], [cm[0], st["maxy"]])

            def dr(p, i=i):
                return at(i - 1, p[0], p[1]) - at(i - 2, p[0], p[1])

            story.append({"V": sum(Fs[i - 1:]), "dcm": at(i - 1, cm[0], cm[1]), "Dcm": dr(cm), "De1": dr(e1), "De2": dr(e2),
                          "cm": cm})
        return {"dir": dir_, "acc": a, "springs": springs, "n": n, "d": d, "W": W, "story": story, "Fs": Fs, "x0": x0,
                "y0": y0, "pts": pts}

    # -- caches over the shared beam (the same beam is used by modules 9, 10 and 11) --
    def hd_geometry(self, dir_, lv=None):
        key = (dir_, lv)
        if key not in self._hg:
            self._hg[key] = super().hd_geometry(dir_, lv)
        return self._hg[key]

    def solve_beam(self, G, F, method, acc, story):
        key = (G["dir"], G["lv"], F, method, acc if method in ("rigid", "rigidT") else None, story)
        if key not in self._sb:
            self._sb[key] = super().solve_beam(G, F, method, acc, story)
        return self._sb[key]

    def chord_scan(self, G, B):
        key = id(B)
        if key not in self._ss:
            self._ss[key] = (B, super().chord_scan(G, B))  # B kept so its id stays unique
        return self._ss[key][1]

    def _core_name(self, idx):
        c = self.core_of(idx)
        return c["name"] if c else None

    # -- module 9: horizontal distribution at one level --
    def hd_wall_level(self, dir_, lv, force="Fx", method="rigid", acc=1):
        R0 = self.compute()
        row = R0["rows"][lv - 1]
        F = row["fpx"] if force == "Fpx" else row["F"]
        rig = method == "rigid"
        base = {"level": lv, "name": row["name"], "dir": dir_, "force": force, "method": method, "F": F,
                "story_h": self.story_h(lv)}
        if rig:
            base["accidental_torsion"] = ACC_LBL[acc]
        G = self.hd_geometry(dir_, lv)
        if "err" in G:
            return {**base, "error": G["err"]}
        B = self.solve_beam(G, F, method, acc, lv)
        if "err" in B:
            return {**base, "error": B["err"]}
        if not F > 0:
            return {**base, "error": "The force at this level is zero or undefined. Check sections 3 to 8."}
        warnings = []
        if abs(B["sumR"] - F) > 1e-6 * max(1, F) or abs(B["resM"]) > 1e-6 * max(1, F * (G["smax"] - G["smin"])):
            warnings.append("Equilibrium check failed. Please report this case.")
        for g in G["groups"]:
            if g["s"] < G["smin"] - EPS or g["s"] > G["smax"] + EPS:
                warnings.append(f"{g['name']} is outside the floor plan.")
        for j, r in enumerate(B["R"]):
            if r < 0:
                warnings.append(f"{G['groups'][j]['name']} has a negative reaction ({r:.2f} k): " +
                                ("torsion reverses the force on this line." if rig else
                                 "the cantilever lifts this line. Check the layout."))

        def sh(V, d):
            return abs(V) / d * 1000 if d > 0 else NAN

        env = None
        if rig:
            Bp, Bm = self.solve_beam(G, F, "rigid", 1, lv), self.solve_beam(G, F, "rigid", -1, lv)

            def env(idx):
                if "err" in Bp or "err" in Bm:
                    return None
                a, b = Bp["RG"]["W"][idx]["f"], Bm["RG"]["W"][idx]["f"]
                same = abs(abs(a) - abs(b)) < 1e-9 * max(1, abs(a))
                return {"force": a if abs(a) >= abs(b) else b, "governs": None if same else ("+5%" if abs(a) >= abs(b) else "-5%")}

        kD = (B["RG"]["P"]["kY"] if dir_ == "Y" else B["RG"]["P"]["kX"]) if rig else NAN
        lines = []
        for j, g in enumerate(G["groups"]):
            f = B["faces"].get(g["s"], {})
            walls = []
            for li, idx in enumerate(g["lines"]):
                l = self.lfrs[idx]
                L, t = self.l_len(l), self.wall_t(l, lv)
                w = {"label": l["label"], "core": self._core_name(idx), "L": L, "t": t}
                if rig:
                    W = B["RG"]["W"][idx]
                    w.update(k=W["k"], share=W["k"] / kD, direct=W["direct"], torsional=W["tors"], force=W["f"],
                             envelope=env(idx), unit_shear_plf=W["f"] / L * 1000)
                else:
                    Rl = B["wallF"][j][li]
                    w.update(k=self.wall_k(l, lv), force=Rl, share_of_F=Rl / F, unit_shear_plf=Rl / L * 1000)
                walls.append(w)
            lines.append({"name": g["name"], "location": g["s"], "R": B["R"][j], "share_of_F": B["R"][j] / F,
                          "diaphragm_shear_plf": [sh(f.get("Vl", NAN), f.get("dl", 0)), sh(f.get("Vr", NAN), f.get("dr", 0))],
                          "walls": walls})
        ch = self.chord_scan(G, B)["max"]
        out = {**base, "q_psf": F * 1000 / G["A"], "V_max": B["Vmax"], "M_max": max(B["Mpos"]["M"], -B["Mneg"]["M"]),
               "M_pos": {"M": B["Mpos"]["M"], "at": B["Mpos"]["s"]}, "M_neg": {"M": B["Mneg"]["M"], "at": B["Mneg"]["s"]},
               "v_max_plf": {"v": B["vmax"]["v"], "at": B["vmax"]["s"]},
               "chord_max": {"T": abs(ch["T"]), "at": ch["s"], "M": ch["M"], "d": ch["d"]},
               "lines": lines, "sum_R": B["sumR"]}
        if rig:
            RG, P = B["RG"], B["RG"]["P"]
            e_in = RG["cm"][0] - P["xR"] if dir_ == "Y" else RG["cm"][1] - P["yR"]
            out["rigid"] = {"cm": RG["cm"], "cr": [P["xR"], P["yR"]], "e_inherent": e_in, "e_accidental": RG["e"],
                            "e_total": e_in + RG["e"], "T": RG["T"], "J": P["J"], "k_total": kD}
            out["perpendicular"] = [
                {"label": l["label"], "core": self._core_name(j), "dir": self.l_dir(l),
                 "location": l["y1"] if self.l_dir(l) == "X" else l["x1"], "L": self.l_len(l), "t": self.wall_t(l, lv),
                 "k": RG["W"][j]["k"], "torsional": RG["W"][j]["f"], "envelope": env(j),
                 "unit_shear_plf": RG["W"][j]["f"] / self.l_len(l) * 1000}
                for j, l in enumerate(self.lfrs) if self.l_dir(l) != dir_]
        out["beam"] = self._beam_out(B)
        out["warnings"] = warnings
        return out

    @staticmethod
    def _beam_out(B):
        """The diaphragm beam, exactly: in interval [a, b), V(s) = V0 − q·(s − a), M(s) = M0 + (V0 + mr)·(s − a) − q·(s − a)²/2
        (about the chord mid-depth) and the axial force N(s) = N0 + nr·(s − a). Reactions act at the supports."""
        return {"supports": B["sup"], "reactions": B["R"],
                "intervals": [{k: iv[k] for k in ("a", "b", "V0", "M0", "q", "mr", "N0", "nr")} for iv in B["ist"]],
                "perpendicular_forces": [{k: c[k] for k in ("label", "a", "b", "f", "c")} for c in B["couples"]]}

    # -- module 10: collectors --
    def collector_calc(self, G, B, gi):
        """Collector along support group gi (kips, ft; unit shears in k/ft). Each wall resists its own force over its length,
        r_u = R_wall / L_wall. C(t) = ∫(v_u − r_u) dt from the start of the line."""
        g, story = G["groups"][gi], B["story"]
        s, bps = g["s"], B["bps"]
        i = next(j for j, b in enumerate(bps) if abs(b - s) < EPS)
        f = B["faces"][bps[i]]
        left = self.cut_at(G["k"], (bps[i - 1] + s) / 2) if i > 0 else []
        right = self.cut_at(G["k"], (s + bps[i + 1]) / 2) if i < len(bps) - 1 else []
        dl, dr = sum(b - a for a, b in left), sum(b - a for a, b in right)
        # force delivered to the line by each side of the diaphragm: left = -V(left face), right = +V(right face)
        qL, qR = (-f["Vl"] / dl if dl > 0 else 0), (f["Vr"] / dr if dr > 0 else 0)
        lines = [self.lfrs[j] for j in g["lines"]]
        walls = [(l["y1"], l["y2"]) if G["k"] == 0 else (l["x1"], l["x2"]) for l in lines]
        R = B["R"][gi]
        rw = [B["wallF"][gi][j] / self.l_len(l) for j, l in enumerate(lines)]

        def in_any(ivs, m):
            return any(a < m < b for a, b in ivs)

        bp = sorted({rnd(x) for iv in left + right + walls for x in iv})
        side_ends = [x for iv in left + right for x in iv]

        def base_label(t, j):
            tags = []
            if j == 0 or j == len(bp) - 1:
                tags.append("diaphragm edge")
            elif any(abs(e - t) < 1e-6 for e in side_ends):
                tags.append("diaphragm step")
            for k, l in enumerate(lines):
                if abs(walls[k][0] - t) < 1e-6:
                    tags.append(l["label"] + " start")
                if abs(walls[k][1] - t) < 1e-6:
                    tags.append(l["label"] + " end")
            return ", ".join(tags)

        nodes, segs, C = [], [], 0.0  # free end of the collector at the start of the line
        for j, t in enumerate(bp):
            nodes.append({"t": t, "C": C, "label": base_label(t, j)})
            if j == len(bp) - 1:
                break
            a, b = t, bp[j + 1]
            m = (a + b) / 2
            vL, vR = (qL if in_any(left, m) else 0), (qR if in_any(right, m) else 0)
            v = vL + vR
            r = sum(rw[k] for k, w in enumerate(walls) if in_any([w], m))
            C0 = C
            C += (v - r) * (b - a)
            segs.append({"a": a, "b": b, "vL": vL, "vR": vR, "v": v, "r": r, "isW": in_any(walls, m), "C0": C0, "C1": C})
        intV = sum(x["v"] * (x["b"] - x["a"]) for x in segs)
        intR = sum(x["r"] * (x["b"] - x["a"]) for x in segs)
        Cmax = max([0.0] + [p["C"] for p in nodes])
        Cmin = min([0.0] + [p["C"] for p in nodes])
        tol = 1e-9 * max(1, abs(R))
        ext = left + right
        outside = bool(ext) and any(a < min(e[0] for e in ext) - EPS or b > max(e[1] for e in ext) + EPS for a, b in walls)
        return {"story": story, "R": R, "rw": rw, "ruMin": min(rw), "ruMax": max(rw), "qL": qL, "qR": qR, "dl": dl, "dr": dr,
                "bp": bp, "segs": segs, "nodes": nodes, "Cstart": nodes[0]["C"], "Cend": C, "intV": intV, "intR": intR,
                "Cmax": Cmax, "Cmin": Cmin, "closes": abs(C) <= tol, "outside": outside}

    @staticmethod
    def col_envelope(cases):
        """Combine the collector of each torsion case: design value at each break point = larger magnitude."""
        n = len(cases[0]["K"]["nodes"])
        if not all(len(c["K"]["nodes"]) == n for c in cases):
            raise ValueError("collector cases do not line up")
        pts = []
        for j, nd in enumerate(cases[0]["K"]["nodes"]):
            vals = [c["K"]["nodes"][j]["C"] for c in cases]
            gi = 0
            for i, v in enumerate(vals):
                if abs(v) > abs(vals[gi]) + 1e-12:
                    gi = i
            pts.append({"t": nd["t"], "vals": vals, "gi": gi, "C": abs(vals[gi]), "label": nd["label"]})
        peaks = [max(c["K"]["Cmax"], -c["K"]["Cmin"]) for c in cases]
        gc = 0
        for i, p in enumerate(peaks):
            if p > peaks[gc] + 1e-12:
                gc = i
        top = pts[0]
        for p in pts:
            if p["C"] > top["C"]:
                top = p

        def worst(f):
            m = 0.0
            for c in cases:
                v = f(c["K"])
                if abs(v) > abs(m):
                    m = v
            return m

        return {"pts": pts, "peaks": peaks, "peak": max(peaks), "gc": gc, "top": top,
                "closes": all(c["K"]["closes"] for c in cases), "worstEnd": worst(lambda K: K["Cend"]),
                "worstInt": worst(lambda K: K["intV"] - K["R"])}

    def collectors_level(self, lv, method="rigid", acc_sel="env", omega0=2.5):
        """Every support line at level lv, in both directions, for the design Fpx; with the rigid model, also the lines
        that carry only the torsional forces of a load perpendicular to them."""
        row = self.compute()["rows"][lv - 1]
        F = row["fpx"]
        base = {"level": lv, "name": row["name"], "Fpx": F}
        GY, GX = self.hd_geometry("Y", lv), self.hd_geometry("X", lv)
        if "err" in GY:
            return {**base, "error": GY["err"]}
        if not F > 0:
            return {**base, "error": "Design Fpx at this level is zero or undefined. Check SDS, Ie and sections 3 to 8."}
        rig = method == "rigid"
        env = rig and acc_sel == "env"
        accs = ([1, -1] if env else [num(acc_sel)]) if rig else [None]

        def mk(d, G, meth, tors, load=None):
            Bs = [{"acc": a, "B": self.solve_beam(G, F, meth, a, lv)} for a in accs]
            e = next((x for x in Bs if "err" in x["B"]), None)
            return {"d": d, "G": G, "Bs": Bs, "tors": tors, "load": load or d, "err": e["B"]["err"] if e else None}

        dirs = [mk("Y", GY, method, False), mk("X", GX, method, False)]
        if rig:
            for d, G in (("X", GX), ("Y", GY)):
                if G["groups"]:
                    dirs.append(mk(d, G, "rigidT", True, "Y" if d == "X" else "X"))
        lines = []
        for D in dirs:
            for gi, g in enumerate(D["G"]["groups"]):
                head = {"line": g["name"], "dir": D["d"], "location": g["s"], "torsion_only": D["tors"], "load_dir": D["load"]}
                if D["err"]:
                    lines.append({**head, "error": D["err"]})
                    continue
                cases = [{"acc": x["acc"], "K": self.collector_calc(D["G"], x["B"], gi)} for x in D["Bs"]]
                E = self.col_envelope(cases)
                K = cases[E["gc"]]["K"]
                warnings = []
                if not E["closes"]:
                    warnings.append(f"Collector does not close: C at end = {E['worstEnd']:.3g} k. Please report this layout.")
                if K["outside"]:
                    warnings.append("A wall on this line extends beyond the diaphragm. The collector still closes, but check "
                                    "the layout.")
                if any(c["K"]["R"] < 0 for c in cases):
                    warnings.append("Negative line reaction in at least one torsion case: the collector reverses sign on this "
                                    "line for that case." if env else
                                    "Negative line reaction (uplift from a cantilever). Collector signs are reversed on this line.")
                lines.append({
                    **head, "cases": [ACC_LBL[c["acc"]] if c["acc"] is not None else None for c in cases],
                    "R": [c["K"]["R"] for c in cases], "sum_L": g["L"], "walls": [self.lfrs[j]["label"] for j in g["lines"]],
                    "ru_plf": [K["ruMin"] * 1000, K["ruMax"] * 1000], "vu_plf": [K["qL"] * 1000, K["qR"] * 1000],
                    "depth": [K["dl"], K["dr"]], "C_max": E["peak"], "omega0_C_max": omega0 * E["peak"],
                    "governs": ACC_LBL[cases[E["gc"]]["acc"]] if env else None, "at": E["top"]["t"], "peaks": E["peaks"],
                    "C_end": E["worstEnd"], "closure_error": E["worstInt"], "closes": E["closes"],
                    "points": [{"at": p["t"], "point": p["label"], "C": p["vals"], "C_env": p["C"], "omega0_C": omega0 * p["C"]}
                               for p in E["pts"]],
                    "segments": [{"from": x["a"], "to": x["b"], "vL_plf": x["vL"] * 1000, "vR_plf": x["vR"] * 1000,
                                  "vu_plf": x["v"] * 1000, "ru_plf": x["r"] * 1000, "wall": x["isW"], "C_start": x["C0"],
                                  "C_end": x["C1"]} for x in K["segs"]],
                    "warnings": warnings})
        if not lines:
            return {**base, "error": "Draw shear walls in section 2 first."}
        n_ok = sum(1 for x in lines if x.get("closes"))
        return {**base, "lines": lines, "closes": f"{n_ok}/{sum(1 for x in lines if 'error' not in x)}"}

    # -- module 11: chords --
    def chords_level(self, dir_, lv, force="Fx", method="rigid", acc=1):
        row = self.compute()["rows"][lv - 1]
        F = row["fpx"] if force == "Fpx" else row["F"]
        base = {"level": lv, "name": row["name"], "dir": dir_, "force": force, "method": method, "F": F}
        if method == "rigid":
            base["accidental_torsion"] = ACC_LBL[acc]
        G = self.hd_geometry(dir_, lv)
        if "err" in G:
            return {**base, "error": G["err"]}
        if not F > 0:
            return {**base, "error": "The force at this level is zero or undefined. Check sections 3 to 8."}
        B = self.solve_beam(G, F, method, acc, lv)
        if "err" in B:
            return {**base, "error": B["err"]}
        scan, rig = self.chord_scan(G, B), method == "rigid"
        Bp = Bm = sp = smn = None
        if rig:
            Bp, Bm = self.solve_beam(G, F, "rigid", 1, lv), self.solve_beam(G, F, "rigid", -1, lv)
            if "err" in Bp or "err" in Bm:
                Bp = Bm = None
            else:
                sp, smn = self.chord_scan(G, Bp), self.chord_scan(G, Bm)
        keys = {}

        def add_k(s0, why):
            k = rnd(s0)
            if k < G["smin"] - EPS or k > G["smax"] + EPS:
                return
            keys[k] = keys[k] + ", " + why if k in keys else why

        for g in G["groups"]:
            add_k(g["s"], g["name"])
        for i, x in enumerate(G["segs"]):
            if i > 0:
                add_k(x["s0"], "depth change")
        if ok(B["Mpos"]["s"]) and B["Mpos"]["M"] > 1e-9:
            add_k(B["Mpos"]["s"], "max +M")
        if ok(B["Mneg"]["s"]) and B["Mneg"]["M"] < -1e-9:
            add_k(B["Mneg"]["s"], "max −M")
        if ok(scan["max"]["s"]):
            add_k(scan["max"]["s"], "max chord force")
        if Bp:
            add_k(sp["max"]["s"], "max chord, +5%")
            add_k(smn["max"]["s"], "max chord, −5%")
        points, gaps = [], False
        for k in sorted(keys):
            Mk, Dk = self.moment_at(B, k), self.chord_depth(G, k)
            gaps = gaps or Dk["gaps"]
            p = {"at": k, "point": keys[k], "M": Mk, "d": Dk["d"], "chords_at": [Dk["lo"], Dk["hi"]],
                 "T": abs(Mk / Dk["d"]) if Dk["d"] > 0 else NAN}
            if rig:
                p["N"] = self.axial_at(B, k)
            if Bp:
                a = abs(self.moment_at(Bp, k) / Dk["d"]) if Dk["d"] > 0 else NAN
                b = abs(self.moment_at(Bm, k) / Dk["d"]) if Dk["d"] > 0 else NAN
                p.update(T_plus_5=a, T_minus_5=b, T_env=max(a, b) if ok(a) else NAN)
            points.append(p)
        warnings = []
        if gaps:
            warnings.append("A cut passes through an opening in the plan; d is taken between the outer edges of the diaphragm. "
                            "Openings will be handled later.")
        if abs(B["resM"]) > 1e-6 * max(1, F * (G["smax"] - G["smin"])):
            warnings.append("The moment diagram does not close at the far end; chord forces near that end are not reliable.")
        mx = scan["max"]
        out = {**base, "max": {"T": abs(mx["T"]), "at": mx["s"], "M": mx["M"], "d": mx["d"]}, "envelope": None,
               "points": points, "warnings": warnings}
        if Bp:
            gp = abs(sp["max"]["T"]) >= abs(smn["max"]["T"])
            g = sp["max"] if gp else smn["max"]
            out["envelope"] = {"T": abs(g["T"]), "governs": "+5%" if gp else "-5%", "at": g["s"],
                               "plus_5": abs(sp["max"]["T"]), "minus_5": abs(smn["max"]["T"])}
        return out

    # -- module 12: shear wall forces --
    def wall_story_shears(self, idx, method, acc):
        """V_i = Σ Fx at and above level i, distributed with the stiffness of story i. Rigid: each level's force at its own
        CM, distributed with story i's stiffness. Flexible: line story shear = Σ line reactions above (tributary beam per
        level), split among the walls on the line by their stiffness in story i."""
        l = self.lfrs[idx]
        dir_ = self.l_dir(l)
        G = self.hd_geometry(dir_)
        if "err" in G:
            return {"err": G["err"]}
        gi = next((j for j, g in enumerate(G["groups"]) if idx in g["lines"]), -1)
        if gi < 0:
            return {"err": "Wall not found on a support line."}
        g, rows = G["groups"][gi], self.compute()["rows"]
        n = len(rows)
        if any(not ok(r["F"]) or not ok(r["h"]) for r in rows):
            return {"err": "Story forces are not defined. Check sections 3 to 8."}
        line_r = None
        if method != "rigid":
            line_r = []
            for r in rows:
                B = self.solve_beam(self.hd_geometry(dir_, r["i"]), r["F"], method, 0, r["i"])
                if "err" in B:
                    return {"err": B["err"]}
                line_r.append(B["R"][gi])
        out, line_above = {}, 0.0
        for i in range(n, 0, -1):
            Vi = sum(r["F"] for r in rows if r["i"] >= i)
            if method == "rigid":
                V = 0.0
                for r in rows:
                    if r["i"] >= i:
                        RG = self.rigid_solve(dir_, r["F"], acc, i, r["i"])
                        if "err" in RG:
                            return {"err": RG["err"]}
                        V += RG["W"][idx]["f"]
            else:
                line_above += line_r[i - 1]
                sk = sum(self.wall_k(self.lfrs[x], i) for x in g["lines"])
                V = line_above * self.wall_k(l, i) / sk if sk > 0 else NAN
            out[i] = {"i": i, "Vstory": Vi, "V": V}
        return {"G": G, "gi": gi, "g": g, "rows": rows, "V": out}

    def ve_calc(self, idx, method, acc):
        SS = self.wall_story_shears(idx, method, acc)
        if "err" in SS:
            return SS
        l = self.lfrs[idx]
        n, L, d = len(SS["rows"]), self.l_len(l), self.l_dir(l)
        zs, core = [0.0] + [r["h"] for r in SS["rows"]], self.core_of(idx)
        lev = []
        for i in range(1, n + 1):
            V = SS["V"][i]["V"]
            Vup = SS["V"][i + 1]["V"] if i < n else 0.0
            lev.append({"i": i, "name": self.label(i), "z": zs[i], "hs": zs[i] - zs[i - 1], "V": V, "f": V - Vup,
                        "Vstory": SS["V"][i]["Vstory"], "t": self.wall_t(l, i), "v": V / L})
        # shear: this wall (segment) alone. Moment: the wall's own level forces, or in a core the full core's, i.e. the level
        # forces of all its segments parallel to this wall
        mates = [w for w in core["walls"] if self.l_dir(self.lfrs[w]) == d] if core else [idx]
        fC = [x["f"] for x in lev]
        if core:
            fC = [0.0] * n
            for w in mates:
                Sw = SS if w == idx else self.wall_story_shears(w, method, acc)
                if "err" in Sw:
                    return Sw
                for i in range(1, n + 1):
                    fC[i - 1] += Sw["V"][i]["V"] - (Sw["V"][i + 1]["V"] if i < n else 0)
        VC, a = {}, 0.0
        for i in range(n, 0, -1):
            a += fC[i - 1]
            VC[i] = a

        def Mat(k):
            """Overturning moment at elevation zs[k] from the level forces above it (ΣF·lever arm)."""
            return sum(fC[x["i"] - 1] * (x["z"] - zs[k]) for x in lev if x["i"] > k)

        # decoupled tension and compression at the wall ends: T = C = M / L at the floor below each story (walls on their own)
        for x in lev:
            x["Mbot"], x["Vc"] = Mat(x["i"] - 1), VC[x["i"]]
            x["T"] = NAN if core else x["Mbot"] / L
        # check: building the moment story by story (V·h summed going down) gives the same base moment
        Msum = sum(x["Vc"] * x["hs"] for x in lev)
        return {"idx": idx, "dir": d, "L": L, "t": self.wall_t_txt(l), "lev": lev, "zs": zs, "H": zs[n], "Mbase": Mat(0),
                "Tbase": NAN if core else Mat(0) / L, "Vbase": lev[0]["V"], "VCbase": VC[1], "Msum": Msum,
                "line": SS["g"]["name"], "core": core, "mates": mates, "acc": acc, "method": method}

    def ve_gov(self, idx, method="rigid", acc_sel="env"):
        """With 'env', solve +5% and -5% and keep the case with the larger base overturning moment (the core's, in a core)."""
        if method == "rigid" and acc_sel == "env":
            Kp, Km = self.ve_calc(idx, "rigid", 1), self.ve_calc(idx, "rigid", -1)
            if "err" in Kp:
                return Kp
            if "err" in Km:
                return Km
            g = Km if abs(Km["Mbase"]) > abs(Kp["Mbase"]) + 1e-9 * max(1, abs(Kp["Mbase"])) else Kp
            return {**g, "env": True, "gov": "-5%" if g is Km else "+5%", "Kp": Kp, "Km": Km}
        return self.ve_calc(idx, method, num(acc_sel) if method == "rigid" else 0)

    def wall_forces(self, idx, method="rigid", acc_sel="env"):
        """Module 12 result for one wall (Fx only), shaped for output."""
        l = self.lfrs[idx]
        K = self.ve_gov(idx, method, acc_sel)
        if "err" in K:
            return {"label": l["label"], "dir": self.l_dir(l), "error": K["err"]}
        C, warnings = K["core"], []
        mt = ", ".join(self.lfrs[w]["label"] for w in K["mates"])
        if C:
            warnings.append(f"{l['label']} is a segment of core {C['name']}. Shear is this segment's own (its t·L share of the "
                            f"core's {K['dir']} shear; segments perpendicular to the force carry none). The overturning moment "
                            f"is the full core's, from all its {K['dir']} segments ({mt}), for flexural design on the composite "
                            "core section.")
        if any(x["f"] < -1e-9 for x in K["lev"]):
            warnings.append("This wall has a negative force at some levels (torsion, or a change in story stiffness, reverses "
                            "it). Values are shown with their sign.")
        oth = (K["Km"] if K["gov"] == "+5%" else K["Kp"]) if K.get("env") else None
        return {
            "label": l["label"], "dir": K["dir"], "line": K["line"], "core": C["name"] if C else None,
            "core_segments": [self.lfrs[w]["label"] for w in K["mates"]] if C else None, "L": K["L"], "t": K["t"],
            "accidental_torsion": K["gov"] if K.get("env") else (ACC_LBL.get(K["acc"], "none") if method == "rigid" else None),
            "envelope": bool(K.get("env")),
            "base_shear": K["Vbase"], "unit_shear_plf": K["Vbase"] / K["L"] * 1000,
            "core_base_shear": K["VCbase"] if C else None, "base_moment": K["Mbase"], "T_C_base": K["Tbase"],
            "T_C_base_other_case": oth["Tbase"] if oth else None,
            "checks": {"moment": abs(K["Msum"] - K["Mbase"]) <= 1e-9 * max(1, abs(K["Mbase"]))},
            "stories": [{"story": x["i"], "level": x["name"], "elevation": x["z"], "h": x["hs"], "t": x["t"],
                         "force_at_level": x["f"], "shear": x["V"], "unit_shear_plf": x["v"] * 1000,
                         "core_shear": x["Vc"] if C else None, "overturning_at_floor_below": x["Mbot"], "T_C": x["T"]}
                        for x in K["lev"]],
            "warnings": warnings,
        }

    # -- module 13: story drift --
    def drift_cases(self):
        """Cantilever solves for X and Y at +5% and -5%."""
        if self._dr is None:
            self._dr = [{"dir": d, "has": any(self.l_dir(l) == d for l in self.lfrs),
                         "cases": [{"acc": a, "B": self.full_solve(d, a, False)} for a in (1, -1)]} for d in ("X", "Y")]
        return self._dr

    def drift_check(self):
        """δx = Cd·δxe/Ie (Eq. 12.8-15), δxe from the cantilever wall model. Allowable Δa = coefficient × h_sx
        (Table 12.12-1, by Risk Category). The larger story drift of the two torsion cases governs."""
        S, n = self.S, self.n
        cd, ie, rc = num(S.get("cd")), num(S.get("ie")), S["rc"]
        if not self.lfrs:
            return {"err": "Draw shear walls in section 2 first."}
        if not cd > 0:
            return {"err": "Enter Cd in Building Inputs (section 1)."}
        if not ie > 0:
            return {"err": "Enter Ie in Building Inputs (section 1)."}
        D = S["dr"]
        typ = "low" if D["type"] == "low" and n <= 4 else "other"
        coef, edge = DRIFT_LIM[typ][rc], D["at"] != "cm"
        rows, roof = [], []
        for g in self.drift_cases():
            if not g["has"]:
                continue
            e = next((c for c in g["cases"] if "err" in c["B"]), None)
            if e:
                return {"err": e["B"]["err"]}
            for c in g["cases"]:
                roof.append({"dir": g["dir"], "acc": c["acc"], "d": cd * abs(c["B"]["story"][n]["dcm"]) / ie})
            for i in range(1, n + 1):
                hsx = self.story_h(i)
                lim = coef * hsx * 12
                per = []
                for c in g["cases"]:
                    s = c["B"]["story"][i]
                    avg, mx = (s["De1"] + s["De2"]) / 2, max(abs(s["De1"]), abs(s["De2"]))
                    per.append({"acc": c["acc"], "dxe": mx if edge else abs(s["Dcm"]),
                                "tor": mx / abs(avg) if abs(avg) > 1e-12 else NAN})
                gv = per[0]
                for p in per[1:]:
                    if p["dxe"] > gv["dxe"]:
                        gv = p
                Dd = cd * gv["dxe"] / ie
                tors = [p["tor"] for p in per if ok(p["tor"])]
                rows.append({"i": i, "dir": g["dir"], "hsx": hsx, "acc": gv["acc"], "dxe": gv["dxe"], "D": Dd,
                             "ratio": Dd / (hsx * 12), "lim": lim, "dcr": Dd / lim, "tor": max(tors) if tors else NAN})
        if not rows:
            return {"err": "Draw shear walls in section 2 first."}

        def big(seq, f):
            m = seq[0]
            for x in seq[1:]:
                if f(x) > f(m):
                    m = x
            return m

        return {"rows": rows, "n": n, "cd": cd, "ie": ie, "rc": rc, "coef": coef, "typ": typ, "edge": edge,
                "worst": big(rows, lambda r: r["dcr"]), "torMax": big(rows, lambda r: r["tor"] if ok(r["tor"]) else 0),
                "ratioMax": big(rows, lambda r: r["ratio"]), "roof": big(roof, lambda r: r["d"])}

    def drift_out(self):
        K = self.drift_check()
        S = self.S
        base = {"Cd": num(S.get("cd")), "Ie": num(S.get("ie")), "risk_category": S["rc"], "at": "cm" if S["dr"]["at"] == "cm" else "edge"}
        if "err" in K:
            return {**base, "error": K["err"]}
        ok_all = all(r["dcr"] <= 1 + 1e-9 for r in K["rows"])
        over = [r for r in K["rows"] if r["dcr"] > 1 + 1e-9]
        T, W = K["torMax"], K["worst"]
        warnings = []
        if S["dr"]["type"] == "low" and K["n"] > 4:
            warnings.append(f'The "4 stories or less" row of Table 12.12-1 needs four stories or less; this building has {K["n"]}, '
                            'so "all other structures" is used.')
        if over:
            warnings.append(f"{len(over)} story drift{'s exceed' if len(over) > 1 else ' exceeds'} the allowable.")
        if not K["edge"] and ok(T["tor"]) and T["tor"] > 1.2:
            warnings.append(f"Story {T['i']} ({T['dir']}) has a torsion ratio of {T['tor']:.2f}, above 1.2 (torsional "
                            f"irregularity Type {'1b' if T['tor'] > 1.4 else '1a'}). In Seismic Design Category C to F, 12.8.6 "
                            "requires the drift at the plan edges.")
        CS = self.compute()["CS"]
        if S["csAuto"] and CS["ok"] and CS["gov"] == "12.8-5":
            warnings.append("Eq. 12.8-5 governs Cs. 12.8.6.1 permits drift to be computed without it; this check keeps it "
                            "(conservative).")

        def irr(t):
            return "1b" if ok(t) and t > 1.4 else "1a" if ok(t) and t > 1.2 else None

        return {**base, "table": "4 stories or less, walls designed for the drifts" if K["typ"] == "low" else "all other structures",
                "coefficient": K["coef"], "ok": ok_all,
                "max_ratio_to_allowable": {"value": W["dcr"], "story": W["i"], "level": self.label(W["i"]), "dir": W["dir"],
                                           "case": ACC_LBL[W["acc"]], "drift_in": W["D"], "allowable_in": W["lim"]},
                "max_drift_ratio": {"value": K["ratioMax"]["ratio"], "story": K["ratioMax"]["i"], "dir": K["ratioMax"]["dir"]},
                "roof_displacement_in": {"value": K["roof"]["d"], "dir": K["roof"]["dir"], "case": ACC_LBL[K["roof"]["acc"]]},
                "max_torsion_ratio": {"value": T["tor"], "story": T["i"], "dir": T["dir"], "irregularity": irr(T["tor"])},
                "stories": [{"story": r["i"], "level": self.label(r["i"]), "dir": r["dir"], "h_sx": r["hsx"],
                             "governs": ACC_LBL[r["acc"]], "drift_elastic_in": r["dxe"], "drift_in": r["D"],
                             "drift_ratio": r["ratio"], "allowable_in": r["lim"], "ratio_to_allowable": r["dcr"],
                             "ok": r["dcr"] <= 1 + 1e-9, "torsion_ratio": r["tor"], "irregularity": irr(r["tor"])}
                            for r in sorted(K["rows"], key=lambda r: (-r["i"], r["dir"]))],
                "warnings": warnings}

    # -- module 2: wall stiffness, cores and the cantilever comparison --
    def stiffness_out(self, acc=1):
        pl, n = self.S["plan"], self.n
        sh, h = pl["kShear"] is not False, self.stiff_h()
        stories = []
        for i in range(1, n + 1):
            P = self.rigid_props(i)
            SK = {"X": P["kX"], "Y": P["kY"]}
            walls = []
            for idx, l in enumerate(self.lfrs):
                w, d = self.wall_stiff(idx, i), self.l_dir(l)
                walls.append({"label": l["label"], "dir": d, "L": self.l_len(l), "t": w["t"],
                              "L_over_h": self.l_len(l) / h if sh and ok(h) else None, "kf": w["kf"], "kv": w["kv"],
                              "k": w["k"], "beta": w["beta"], "share_of_dir": w["k"] / SK[d] if SK[d] > 0 and ok(w["k"]) else NAN,
                              "core": w["core"]["name"] if w.get("core") else None, "core_share": w.get("share")})
            stories.append({"story": i, "level": self.label(i), "h": self.story_h(i), "kX": P["kX"], "kY": P["kY"],
                            "cr": [P["xR"], P["yR"]], "J": P["J"], "walls": walls})
        T = self.core_topo()
        cores = []
        for c in T["cores"]:
            by = []
            for i in range(1, n + 1):
                s = self.core_sec(c, i)
                if not s:
                    by.append({"story": i, "error": "A wall has no thickness at this story."})
                    continue
                KX, KY = self.core_stiff(c, i, "X"), self.core_stiff(c, i, "Y")

                def solo(d):
                    return sum(self.solo_stiff(self.lfrs[j], i)["k"] for j in c["walls"] if self.l_dir(self.lfrs[j]) == d)

                def rat(K, d):
                    return K["k"] / solo(d) if K and ok(K["k"]) and solo(d) > 0 else NAN

                by.append({"story": i, "A": s["A"], "centroid": [s["xc"], s["yc"]], "I_for_X_load": s["Iy"],
                           "I_for_Y_load": s["Ix"], "Av_X": s["AvX"], "Av_Y": s["AvY"], "kX": KX["k"] if KX else NAN,
                           "kY": KY["k"] if KY else NAN, "vs_separate_walls_X": rat(KX, "X"), "vs_separate_walls_Y": rat(KY, "Y")})
            cores.append({"name": c["name"], "walls": [self.lfrs[j]["label"] for j in c["walls"]], "stories": by})
        joints = [{"key": j["key"], "x": j["x"], "y": j["y"], "type": j["type"], "walls": [self.lfrs[w]["label"] for w in j["walls"]],
                   "core": j["core"]["name"] if j["core"] else None, "monolithic": j["on"]} for j in T["joints"]]
        return {"shear_deformation": sh, "stiffness_height": h, "icr": self.icr(), "fc": num(pl.get("fc")), "Ec": self.Ec(),
                "kappa": KAPPA, "E_over_G": E_G, "stories": stories, "cores": cores, "joints": joints,
                "cantilever": {"accidental_torsion": ACC_LBL[acc], "X": self._cmp_out("X", acc), "Y": self._cmp_out("Y", acc)}}

    def _cmp_out(self, dir_, acc):
        """Point springs (A) and cantilever walls (B) for the same loading: story shears, k_eff = V / drift, wall drift."""
        A, B = self.full_solve(dir_, acc, True), self.full_solve(dir_, acc, False)
        if "err" in A or "err" in B:
            return {"error": A.get("err") or B.get("err")}
        out = []
        for i in range(1, self.n + 1):
            s = B["story"][i]
            out.append({"story": i, "level": self.label(i), "story_shear": s["V"], "displacement_cm_in": s["dcm"],
                        "drift_cm_in": s["Dcm"], "edge_drift_in": max(abs(s["De1"]), abs(s["De2"])),
                        "walls": [{"label": l["label"], "dir": self.l_dir(l), "t": self.wall_t(l, i), "k_spring": self.wall_k(l, i),
                                   "k_eff": B["W"][j]["keff"][i], "V_springs": A["W"][j]["V"][i], "V_cantilever": B["W"][j]["V"][i],
                                   "drift_in": B["W"][j]["drift"][i]} for j, l in enumerate(self.lfrs)]})
        return {"roof_displacement_cm_in": B["story"][self.n]["dcm"], "stories": out}

    def analyze(self, hd_force="Fx", hd_acc=1, hd_method="rigid", col_method="rigid", col_acc="env", omega0=2.5,
                ch_force="Fx", ch_method="rigid", ch_acc=1, ve_method="rigid", ve_acc="env", stiffness_acc=1, **_):
        """Modules 1 to 13 for shear walls. Accidental torsion: 1, -1 or 0 ('env' for the ±5% envelope in 10 and 12)."""
        S, warnings = self.S, []
        R0 = self.compute()
        sds, sd1, ie = R0["sds"], num(S.get("sd1")), R0["ie"]
        sdc = sdc_of(sds, sd1, S["rc"]) if ok(sds) and ok(sd1) else None
        CS = R0["CS"]
        if S["csAuto"] and not CS["ok"]:
            warnings.append(CS["why"] + " The typed Cs is used until then.")
        if not S["csAuto"] and CS["ok"] and abs(CS["cs"] - R0["cs"]) > 5e-5:
            warnings.append(f"The typed Cs = {R0['cs']:.4f} differs from the calculated {CS['cs']:.4f} (Eq. {CS['gov']}).")
        if CS["ok"] and CS["mxEq"] == "12.8-3" and not num(S.get("TL")) > 0 and CS["T"] > 4:
            warnings.append("T is over 4 s and T_L is blank: enter T_L to check whether Eq. 12.8-4 applies.")
        ie_rc = IE_BY_RISK[S["rc"]]
        if ok(ie) and abs(ie - ie_rc) > 1e-9:
            warnings.append(f"Table 1.5-2 gives Ie = {ie_rc:g} for Risk Category {S['rc']}; {ie:g} is entered.")
        warnings += [text for _, text in self.messages(R0)]
        warnings += self._load_area_warnings()
        st, rows = self.poly_stats(), R0["rows"]
        top = list(reversed(rows))
        result = {
            "lateral_system": "shear_wall",
            "inputs": {"project": S.get("project", ""), "cs": R0["cs"],
                       "cs_source": f"calculated, Eq. {CS['gov']} governs" if R0["csCalcd"] else "input",
                       "ie": ie, "sds": sds, "sd1": sd1, "S1": num(S.get("S1")), "TL": num(S.get("TL")), "sdc": sdc,
                       "R": num(S.get("R")), "Cd": num(S.get("cd")), "risk_category": S["rc"], "sys": S.get("sys"),
                       "ct": num(S.get("ct")), "x": num(S.get("xexp")), "omega0": omega0},
            "seismic_coefficient": {"calculated": R0["csCalcd"], "cs_from_R": S["csAuto"], "used": R0["cs"],
                                    "governs": CS.get("gov"), "R_over_Ie": CS.get("Ri"), "eq_12_8_2": CS.get("eq2"),
                                    "upper_limit": {"eq": CS.get("mxEq"), "value": CS.get("mx")}, "eq_12_8_5": CS.get("min5"),
                                    "eq_12_8_6": CS.get("min6"), "why_not": CS["why"] or None},
            "plan": {"area": self.plan_area(), "perimeter": st["per"] if st else NAN,
                     "centroid": [st["cx"], st["cy"]] if st else None,
                     "Lx": st["maxx"] - st["minx"] if st else NAN, "Ly": st["maxy"] - st["miny"] if st else NAN,
                     "walls": [{"label": l["label"], "dir": self.l_dir(l), "x1": l["x1"], "y1": l["y1"], "x2": l["x2"],
                                "y2": l["y2"], "L": self.l_len(l), "t_by_story": l["ts"], "core": self._core_name(j)}
                               for j, l in enumerate(self.lfrs)]},
            "period": {**R0["P"], "k": R0["k"]},
            "base_shear": {"W": R0["W"], "cs": R0["cs"], "V": R0["V"], "sum_F": R0["sumF"], "sum_Cvx": R0["sumCvx"]},
            "levels": _levels_out(self, rows, st),
            "stiffness": self.stiffness_out(stiffness_acc),
            "horizontal_distribution": {
                "force": hd_force, "method": hd_method, "accidental_torsion": ACC_LBL[hd_acc] if hd_method == "rigid" else None,
                "X": [self.hd_wall_level("X", r["i"], hd_force, hd_method, hd_acc) for r in top],
                "Y": [self.hd_wall_level("Y", r["i"], hd_force, hd_method, hd_acc) for r in top]},
            "collectors": {"force": "Fpx", "method": col_method,
                           "accidental_torsion": (col_acc if col_acc == "env" else ACC_LBL[col_acc]) if col_method == "rigid" else None,
                           "omega0": omega0, "levels": [self.collectors_level(r["i"], col_method, col_acc, omega0) for r in top]},
            "chords": {"force": ch_force, "method": ch_method, "accidental_torsion": ACC_LBL[ch_acc] if ch_method == "rigid" else None,
                       "X": [self.chords_level("X", r["i"], ch_force, ch_method, ch_acc) for r in top],
                       "Y": [self.chords_level("Y", r["i"], ch_force, ch_method, ch_acc) for r in top]},
            "wall_forces": {"force": "Fx", "method": ve_method,
                            "accidental_torsion": (ve_acc if ve_acc == "env" else ACC_LBL[ve_acc]) if ve_method == "rigid" else None,
                            "walls": [self.wall_forces(j, ve_method, ve_acc) for j in range(len(self.lfrs))]},
            "drift": self.drift_out(),
            "warnings": warnings,
        }
        return clean(result)


def analyze(project, hd_force="Fx", hd_acc=1, ve_acc="env", R=3.0, **options):
    """Run the engine on a dashboard project dict. Returns a JSON-safe dict.

    hd_force: "Fx" or "Fpx" for module 9.     hd_acc: 1, -1 or 0 (module 9 accidental torsion).
    ve_acc: "env", 1, -1 or 0 (module 12).     R: braced frames only, used to compute Cs when the project has none
    (shear walls take R from the project, project["R"]).
    options (shear walls): hd_method, col_method, col_acc, omega0, ch_force, ch_method, ch_acc, ve_method, stiffness_acc;
    see ShearWallEngine.analyze.
    """
    return Engine(project).analyze(hd_force=hd_force, hd_acc=hd_acc, ve_acc=ve_acc, R=R, **options)
