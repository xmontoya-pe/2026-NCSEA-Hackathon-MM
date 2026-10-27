"""Candidate column grid generation and clipping to the building outline.

Spec section 4.2 (decisions log, spec section 7; docs/ASSUMPTIONS.md G-*). Steel only for now.

Candidates are built axis by axis. The outline's vertex coordinates are required grid
lines (so re-entrant corners get columns); each segment between them is divided by:

- Strategy A: the recognized ``dimension_lines``, each dimension span either kept,
  subdivided into 2 or 3 equal bays, or merged with its neighbor (lines skipped).
- Strategy B: equal bays near each bay size in the bay study, or bays of that size
  plus one odd end bay.

Every bay must lie within ``min_bay_ft``..``max_bay_ft`` (a segment shorter than
``min_bay_ft`` is kept as one bay). X and Y options are combined into grids.

``layout`` places columns at grid intersections covered by the outline, flags columns
within 2 ft of the outline edge, removes columns strictly inside ``no_columns`` zones
(in that story and every story above; ASSUMPTIONS G-3), and groups grid cells into bays
per framed level, merging cells around removed columns into the smallest long-span bay.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from string import ascii_uppercase

from shapely.geometry import Point, Polygon, box

from gravity.loads import SLAB_ON_GRADE, ZoneMap
from gravity.models import GravityInput

TOL_FT = 1e-6
PERIMETER_FLAG_FT = 2.0  # spec 4.2


@dataclass(frozen=True)
class Grid:
    grid_id: str  # e.g. "X:30x5 Y:30x3"
    x_ft: tuple[float, ...]
    y_ft: tuple[float, ...]
    strategy: str  # strategies that produced it, e.g. "A+B" or "B"


@dataclass(frozen=True)
class BayRect:
    bay_id: str  # corner to corner, e.g. "A1-B2"
    i0: int  # first x cell
    i1: int  # last x cell
    j0: int
    j1: int
    x0: float
    x1: float
    y0: float
    y1: float
    supported: bool = True  # False: no column arrangement can support it

    @property
    def span_x_ft(self) -> float:
        return self.x1 - self.x0

    @property
    def span_y_ft(self) -> float:
        return self.y1 - self.y0

    @property
    def merged(self) -> bool:
        return self.i1 > self.i0 or self.j1 > self.j0

    @property
    def polygon(self) -> Polygon:
        return box(self.x0, self.y0, self.x1, self.y1)


@dataclass
class GridLayout:
    grid: Grid
    columns_by_story: dict[str, frozenset[tuple[int, int]]]  # story base level -> (i, j)
    bays_by_level: dict[str, list[BayRect]]  # framed level -> bays
    warnings: list[str] = field(default_factory=list)

    @property
    def column_count(self) -> int:
        """Distinct column positions over all stories."""
        return len(frozenset().union(*self.columns_by_story.values())) if self.columns_by_story else 0


# ---------------------------------------------------------------------------
# Candidate grids
# ---------------------------------------------------------------------------


def generate_grids(
    inp: GravityInput, bay_sizes_ft: list[float] | None = None, system: str = "steel"
) -> tuple[list[Grid], list[str]]:
    """Candidate grids for one system, sorted by grid id.

    ``bay_sizes_ft`` defaults to every span in the bay study (beam or girder) within
    ``min_bay_ft``..``max_bay_ft``.
    """
    c = inp.constraints
    if bay_sizes_ft is None:
        from gravity.db_adapter import available_spans  # SQL stays in the adapter

        beams, girders = available_spans(system, "floor")
        bay_sizes_ft = sorted({s for s in beams + girders if c.min_bay_ft <= s <= c.max_bay_ft})

    outline = Polygon(inp.outline)
    min_x, min_y, max_x, max_y = outline.bounds
    xs_required = _required_lines([p[0] for p in inp.outline], min_x, max_x)
    ys_required = _required_lines([p[1] for p in inp.outline], min_y, max_y)

    x_options = _axis_options(xs_required, inp.dimension_lines.x_ft, bay_sizes_ft,
                              c.min_bay_ft, c.max_bay_ft)
    y_options = _axis_options(ys_required, inp.dimension_lines.y_ft, bay_sizes_ft,
                              c.min_bay_ft, c.max_bay_ft)

    warnings = []
    if not x_options or not y_options:
        warnings.append(
            f"No grid fits bay limits {c.min_bay_ft:g}-{c.max_bay_ft:g} ft "
            f"({len(x_options)} X options, {len(y_options)} Y options)"
        )
    grids = [
        Grid(f"X:{_compress(xl)} Y:{_compress(yl)}", xl, yl, _merge_labels(xs, ys))
        for xl, xs in x_options.items()
        for yl, ys in y_options.items()
    ]
    return sorted(grids, key=lambda g: g.grid_id), warnings


def _required_lines(coords: list[float], lo: float, hi: float) -> list[float]:
    return sorted({_r(v) for v in coords} | {_r(lo), _r(hi)})


def _axis_options(
    required: list[float], dims: list[float], sizes: list[float], min_bay: float, max_bay: float
) -> dict[tuple[float, ...], str]:
    """Distinct line sets for one axis -> strategy label ("A", "B" or "A+B")."""
    segments = list(zip(required[:-1], required[1:]))
    found: dict[tuple[float, ...], set[str]] = {}

    def add(per_segment, label):
        if any(part is None for part in per_segment):
            return
        lines = tuple(sorted({_r(v) for part in per_segment for v in part}))
        if _bays_ok(lines, segments, min_bay, max_bay):
            found.setdefault(lines, set()).add(label)

    for variant in ("fit", "fine", "merge"):
        add([_strategy_a(a, b, dims, variant, min_bay, max_bay) for a, b in segments], "A")
    for size in sizes:
        for rounding in (math.floor, math.ceil):
            add([_equal(a, b, size, rounding, min_bay, max_bay) for a, b in segments], "B")
        add([_with_odd_bay(a, b, size, min_bay, max_bay) for a, b in segments], "B")
    return {lines: "+".join(sorted(labels)) for lines, labels in found.items()}


def _strategy_a(a, b, dims, variant, min_bay, max_bay):
    points = sorted({a, b} | {_r(d) for d in dims if a + TOL_FT < d < b - TOL_FT})
    if variant == "merge":  # skip lines while the merged span still fits
        merged = [points[0]]
        for p in points[1:]:
            if len(merged) > 1 and p - merged[-2] <= max_bay + TOL_FT:
                merged[-1] = p
            else:
                merged.append(p)
        points = merged
    lines = [points[0]]
    for p, q in zip(points[:-1], points[1:]):
        span = q - p
        m = next((m for m in (1, 2, 3) if span / m <= max_bay + TOL_FT), None)
        if m is None:
            return None
        if variant == "fine" and m < 3 and span / (m + 1) >= min_bay - TOL_FT:
            m += 1
        lines.extend(p + span * k / m for k in range(1, m + 1))
    return lines


def _equal(a, b, size, rounding, min_bay, max_bay):
    length = b - a
    n = max(1, int(rounding(round(length / size, 9))))
    bay = length / n
    if not (min_bay - TOL_FT <= bay <= max_bay + TOL_FT) and not (n == 1 and length < min_bay):
        return None
    return [a + bay * k for k in range(n + 1)]


def _with_odd_bay(a, b, size, min_bay, max_bay):
    """Bays of ``size`` from ``a`` plus one odd end bay of at least ``min_bay``."""
    length = b - a
    k = int(math.floor(length / size + TOL_FT))
    remainder = length - k * size
    if k == 0:
        return None
    if remainder < TOL_FT:  # exact multiple: no odd bay needed
        return [a + size * i for i in range(k + 1)]
    if remainder < min_bay - TOL_FT or remainder > max_bay + TOL_FT:
        return None
    return [a + size * i for i in range(k + 1)] + [b]


def _bays_ok(lines, segments, min_bay, max_bay) -> bool:
    for p, q in zip(lines[:-1], lines[1:]):
        span = q - p
        if span > max_bay + TOL_FT:
            return False
        if span < min_bay - TOL_FT and not any(
            abs(p - a) < TOL_FT and abs(q - b) < TOL_FT for a, b in segments
        ):
            return False  # only a whole short segment may be below min_bay
    return True


def _compress(lines: tuple[float, ...]) -> str:
    """(0, 30, 60, 90) -> '30x3'; (0, 26, 52, 78, 104, 130, 150) -> '26x5+20'."""
    spans = [round(q - p, 2) for p, q in zip(lines[:-1], lines[1:])]
    parts, i = [], 0
    while i < len(spans):
        j = i
        while j + 1 < len(spans) and spans[j + 1] == spans[i]:
            j += 1
        count = j - i + 1
        parts.append(f"{spans[i]:g}x{count}" if count > 1 else f"{spans[i]:g}")
        i = j + 1
    return "+".join(parts)


def _merge_labels(*labels: str) -> str:
    return "+".join(sorted(set("+".join(labels).split("+"))))


def _r(value: float) -> float:
    return round(float(value), 6)


# ---------------------------------------------------------------------------
# Layout: columns and bays for one grid
# ---------------------------------------------------------------------------


def x_label(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    label = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        label = ascii_uppercase[rem] + label
    return label


def column_id(i: int, j: int) -> str:
    return f"{x_label(i)}-{j + 1}"


def layout(inp: GravityInput, zone_map: ZoneMap, grid: Grid) -> GridLayout:
    outline = Polygon(inp.outline)
    xs, ys = grid.x_ft, grid.y_ft
    warnings: list[str] = []

    covers = outline.buffer(TOL_FT)
    points = set()
    for i, x in enumerate(xs):
        for j, y in enumerate(ys):
            pt = Point(x, y)
            if not covers.covers(pt):
                continue
            points.add((i, j))
            edge = outline.exterior.distance(pt)
            if TOL_FT < edge <= PERIMETER_FLAG_FT:
                warnings.append(
                    f"Column {column_id(i, j)} is {edge:.2f} ft from the outline edge; "
                    "consider a perimeter grid line"
                )

    levels = list(zone_map.levels)  # lowest to highest
    removed: set[tuple[int, int]] = set()
    columns_by_story: dict[str, frozenset[tuple[int, int]]] = {}
    for story in levels[:-1]:  # the roof has no story above it
        for zone in zone_map.levels[story].no_column_zones:
            inner = zone.geometry.buffer(-TOL_FT)
            for i, j in sorted(points - removed):
                if inner.contains(Point(xs[i], ys[j])):
                    removed.add((i, j))
                    warnings.append(
                        f"Column {column_id(i, j)} removed for no-column zone {zone.zone_id} "
                        f"(story {story} and above)"
                    )
        columns_by_story[story] = frozenset(points - removed)

    cells = {
        (i, j)
        for i in range(len(xs) - 1)
        for j in range(len(ys) - 1)
        if outline.covers(box(xs[i], ys[j], xs[i + 1], ys[j + 1]).centroid)
    }
    bays_by_level: dict[str, list[BayRect]] = {}
    for below, level in zip(levels[:-1], levels[1:]):
        if zone_map.levels[level].role == SLAB_ON_GRADE:
            continue
        bays, notes = _bays_for_level(cells, columns_by_story[below], xs, ys)
        bays_by_level[level] = bays
        warnings.extend(f"{level}: {n}" for n in notes)
    return GridLayout(grid, columns_by_story, bays_by_level, warnings)


def _bays_for_level(cells, support, xs, ys) -> tuple[list[BayRect], list[str]]:
    """Group cells into bays whose four corners all have columns (smallest span first)."""
    nx, ny = len(xs) - 1, len(ys) - 1

    def corners_ok(i0, i1, j0, j1):
        return all(p in support for p in ((i0, j0), (i1 + 1, j0), (i0, j1 + 1), (i1 + 1, j1 + 1)))

    assigned: set[tuple[int, int]] = set()
    bays: list[BayRect] = []
    notes: list[str] = []
    for ci, cj in sorted(c for c in cells if not corners_ok(c[0], c[0], c[1], c[1])):
        if (ci, cj) in assigned:
            continue
        best = None
        for i0 in range(ci + 1):
            for i1 in range(ci, nx):
                for j0 in range(cj + 1):
                    for j1 in range(cj, ny):
                        block = [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]
                        if any(b not in cells or b in assigned for b in block):
                            continue
                        if not corners_ok(i0, i1, j0, j1):
                            continue
                        dx, dy = xs[i1 + 1] - xs[i0], ys[j1 + 1] - ys[j0]
                        key = (max(dx, dy), dx * dy, i0, j0, i1, j1)
                        if best is None or key < best[0]:
                            best = (key, (i0, i1, j0, j1), block)
        if best is None:
            bays.append(_rect(ci, ci, cj, cj, xs, ys, supported=False))
            assigned.add((ci, cj))
            notes.append(f"bay {bays[-1].bay_id} has no column arrangement that can support it")
            continue
        _, (i0, i1, j0, j1), block = best
        assigned.update(block)
        bays.append(_rect(i0, i1, j0, j1, xs, ys))

    for i, j in sorted(cells - assigned):
        bays.append(_rect(i, i, j, j, xs, ys))
    return sorted(bays, key=lambda b: (b.j0, b.i0)), notes


def _rect(i0, i1, j0, j1, xs, ys, supported=True) -> BayRect:
    return BayRect(
        bay_id=f"{x_label(i0)}{j0 + 1}-{x_label(i1 + 1)}{j1 + 2}",
        i0=i0, i1=i1, j0=j0, j1=j1,
        x0=xs[i0], x1=xs[i1 + 1], y0=ys[j0], y1=ys[j1 + 1],
        supported=supported,
    )
