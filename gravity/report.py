"""Plain-text summary tables for reviewing candidate grids (spec 6, prompt 5).

``python -m gravity summary <input.json>`` prints every steel candidate grid ranked by
``bays.CandidateResult.rank_key`` (feasible, within depth, cheapest framing), then the
bay-by-bay framing of the best one. Framing only: columns are not sized yet, and costs
use the placeholder rates in ``config/costs.json``.
"""

from __future__ import annotations

from collections import Counter

from gravity.bays import CandidateResult, evaluate, evaluate_all
from gravity.columns import ColumnStack, column_config, design_columns, seismic_weights
from gravity.grid import generate_grids, layout
from gravity.loads import resolve_zones
from gravity.models import GravityInput


def summary_table(inp: GravityInput, system: str = "steel", top: int = 15) -> str:
    results, warnings = evaluate_all(inp, system)
    lines = [
        f"{system.upper()} candidate grids for {inp.project_name!r}: {len(results)} candidates "
        "(framing only; columns not sized; placeholder costs)",
        "Ranked by: infeasible bays, then bays over depth, then total framing cost "
        "(interim until scoring.py; column cost not included yet)",
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


def column_report(
    inp: GravityInput, column: str, grid_id: str | None = None, system: str = "steel"
) -> str:
    """Hand-check trace for one column (spec 6, prompt 6), plus column totals and seismic
    weights for its grid. ``grid_id`` defaults to the best-ranked grid."""
    zone_map = resolve_zones(inp)
    if grid_id is None:
        candidate = evaluate_all(inp, system)[0][0]
    else:
        grids, _ = generate_grids(inp, system=system)
        match = [g for g in grids if g.grid_id == grid_id]
        if not match:
            return f"grid {grid_id!r} not found; candidates: " + ", ".join(g.grid_id for g in grids)
        candidate = evaluate(inp, zone_map, layout(inp, zone_map, match[0]), system)
    design = design_columns(inp, zone_map, candidate, system)
    ids = [c.column_id for c in design.columns]
    if column not in ids:
        return f"column {column!r} not in grid {candidate.grid_id}; columns: {', '.join(ids)}"

    lines = column_trace(design.column(column), candidate.grid_id, column_config(system))
    lines += [
        "",
        f"All columns, grid {candidate.grid_id}: {design.tons:.2f} tons, "
        f"${design.cost_usd:,.0f} (placeholder ${design.cost_per_ton_usd:,.0f}/ton), "
        f"{design.carbon_kgco2e:,.0f} kgCO2e ({design.carbon_kg_per_tonne:g} kg/t x 0.9072 t/ton), "
        f"max ratio {design.max_ratio:.3f}",
        "",
        "Seismic weight (D + partitions + 25% storage LL + 20% snow > 30 psf + half columns):",
    ]
    for s in seismic_weights(inp, zone_map, candidate, design, system):
        lines.append(f"  {s.level:<6} W = {s.weight_kips:9.1f} kips   center of mass = "
                     f"({s.center_of_mass_ft[0]:.2f}, {s.center_of_mass_ft[1]:.2f}) ft")
    if design.warnings:
        lines += ["", "Column warnings:"] + [f"  {w}" for w in design.warnings]
    return "\n".join(lines)


def column_trace(stack: ColumnStack, grid_id: str, cfg: dict) -> list[str]:
    """Every number for one column: tributary loads, reduction, combos, E3 capacity."""
    k, fy = cfg["k"], cfg["fy_ksi"]
    lines = [
        f"Column {stack.column_id} at ({stack.x_ft:g}, {stack.y_ft:g}) ft, grid {grid_id}: "
        f"{stack.kind} column, KLL = {stack.kll:g}",
        f"Fy = {fy:g} ksi, E = {cfg['e_ksi']:g} ksi, K = {k:g}, target Pu/phiPn <= "
        f"{cfg['target_ratio']:g}, splice every {cfg['splice_every_n_levels']} stories",
        "",
        "Tributary load by level (each bay's area / 4 to its corner columns):",
    ]
    for level, x in stack.level_loads.items():
        lines.append(
            f"  {level}: AT = {x.at_sf:g} sf; D = {x.d_kips:.2f} k; L reducible = "
            f"{x.l_red_kips:.2f} k; L non-reducible = {x.l_nonred_kips:.2f} k; "
            f"Lr0 = {x.lr0_kips:.2f} k; S = {x.s_kips:.2f} k"
        )
        lines += [f"      {note}" for note in x.notes]
    for s in reversed(stack.stories):
        lines += [
            "",
            f"Story {s.story} ({s.height_ft:g} ft), carries {', '.join(s.supports)}:",
            f"  Pd  = {s.pd_kips:.2f} k (includes column self-weight {s.self_weight_kips:.3f} k)",
            f"  Pl  = {s.pl_kips:.2f} k (unreduced {s.pl_unreduced_kips:.2f} k): {s.ll_note}",
            f"  Plr = {s.plr_kips:.2f} k: {s.lr_note}",
            f"  Ps  = {s.ps_kips:.2f} k",
            "  combos: " + "; ".join(f"{n} = {v:.2f}" for n, v in s.combos.items()),
            f"  Pu  = {s.pu_kips:.2f} k ({s.governing_combo})",
        ]
        if s.shape is not None:
            lines += [
                f"  {s.capacity.note(s.shape, k, fy)}",
                f"  ratio = Pu/phiPn = {s.pu_kips:.2f}/{s.capacity.phi_pn_kips:.1f} = {s.ratio:.3f}",
            ]
    return lines


def _grouped(warnings: list[str]) -> list[tuple[str, int]]:
    """Collapse per-bay warnings that differ only in their 'LEVEL bay ID: ' prefix."""
    counts: Counter[str] = Counter()
    for w in warnings:
        head, sep, rest = w.partition(": ")
        counts[rest if sep and " bay " in head else w] += 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
