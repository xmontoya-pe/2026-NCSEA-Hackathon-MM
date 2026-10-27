"""Plain-text summary tables for reviewing candidate grids (spec 6, prompt 5).

``python -m gravity summary <input.json>`` prints every steel candidate grid ranked by
``bays.CandidateResult.rank_key`` (feasible, within depth, cheapest framing), then the
bay-by-bay framing of the best one. Framing only: columns are not sized yet, and costs
use the placeholder rates in ``config/costs.json``.
"""

from __future__ import annotations

from collections import Counter

from gravity.bays import CandidateResult, evaluate_all
from gravity.models import GravityInput


def summary_table(inp: GravityInput, system: str = "steel", top: int = 15) -> str:
    results, warnings = evaluate_all(inp, system)
    lines = [
        f"{system.upper()} candidate grids for {inp.project_name!r}: {len(results)} candidates "
        "(framing only; columns not sized; placeholder costs)",
        "",
        f"{'rank':>4}  {'grid':<28} {'cols':>4} {'bays':>4} {'infeas':>6} {'>depth':>6} "
        f"{'max in':>6} {'$/sf':>7} {'kgCO2e/sf':>9}",
    ]
    for rank, r in enumerate(results[:top], start=1):
        lines.append(_candidate_row(rank, r))
    if len(results) > top:
        lines.append(f"  ... {len(results) - top} more")

    if results:
        best = results[0]
        lines += ["", f"Best: {best.grid_id}  x_ft={list(best.layout.grid.x_ft)}  "
                      f"y_ft={list(best.layout.grid.y_ft)}", ""]
        lines.append(
            f"{'level':<6} {'bay':<8} {'spans ft':<11} {'occupancy':<22} {'LL':>4} "
            f"{'criterion':<15} {'beam':<18} {'girder':<8} {'depth':>6} {'allow':>6} {'$/sf':>7}"
        )
        for b in best.bays:
            lines.append(_bay_row(b))
        lines += ["", "Warnings (best candidate, grouped):"]
        for message, count in _grouped(warnings + best.warnings):
            lines.append(f"  {count:>3} x {message}")
    return "\n".join(lines)


def _candidate_row(rank: int, r: CandidateResult) -> str:
    area = r.area_sf or 1.0
    return (
        f"{rank:>4}  {r.grid_id:<28} {r.layout.column_count:>4} {len(r.bays):>4} "
        f"{r.n_infeasible:>6} {r.n_over_depth:>6} {r.max_depth_in:>6.1f} "
        f"{r.framing_cost_usd / area:>7.2f} {r.framing_carbon_kgco2e / area:>9.2f}"
    )


def _bay_row(b) -> str:
    spans = f"{b.rect.span_x_ft:g}x{b.rect.span_y_ft:g}"
    if b.framing is None:
        framing = f"{'INFEASIBLE':<18} {'':<8} {'':>6}"
        cost = f"{'':>7}"
    else:
        flag = "" if b.depth_ok else "!"
        framing = f"{b.framing.beam:<18} {b.framing.girder:<8} {b.framing.depth_in:>5.1f}{flag:1}"
        cost = f"{b.framing.cost_per_sf:>7.2f}"
    return (
        f"{b.level:<6} {b.rect.bay_id:<8} {spans:<11} {b.loads.governing_occupancy:<22} "
        f"{b.loads.lookup_ll_psf:>4g} {str(b.loads.criterion or 'Strength'):<15} {framing} "
        f"{b.allowable_depth_in:>6.1f} {cost}"
    )


def _grouped(warnings: list[str]) -> list[tuple[str, int]]:
    """Collapse per-bay warnings that differ only in their 'LEVEL bay ID: ' prefix."""
    counts: Counter[str] = Counter()
    for w in warnings:
        head, sep, rest = w.partition(": ")
        counts[rest if sep and " bay " in head else w] += 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
