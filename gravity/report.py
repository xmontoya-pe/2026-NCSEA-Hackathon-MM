"""Plain-text tables for reviewing results.

- ``python -m gravity report <input.json>...`` (spec 6, prompts 7 and 8): per input and
  output system, the best scheme and its alternates as ranked by ``optimizer.optimize``
  (framing + columns, weighted score), with tons split into framing vs columns, and the
  warnings; then a comparison of the best scheme of each system.
- ``python -m gravity summary <input.json>`` (prompt 5): every candidate grid by framing
  alone (``bays.CandidateResult.rank_key``) and the bay-by-bay framing of the first one.
- ``python -m gravity column <input.json> --column B-2`` (prompt 6): one column, every
  number.

Costs use the placeholder rates in ``config/costs.json``.
"""

from __future__ import annotations

from collections import Counter

from gravity.bays import CandidateResult, evaluate, evaluate_all
from gravity.columns import ColumnStack, column_config, design_columns, seismic_weights
from gravity.grid import generate_grids, layout
from gravity.loads import resolve_zones
from gravity.models import GravityInput
from gravity.optimizer import N_ALTERNATES, Evaluated, SystemResult, optimize


def scheme_report(inp: GravityInput, system: str = "steel", result: SystemResult | None = None
                  ) -> str:
    """Best scheme and alternates for one input and output system (spec 6, prompts 7, 8).
    Tons are steel: beams/girders (steel), rebar + PT (concrete); columns likewise."""
    result = optimize(inp, system) if result is None else result
    lines = [
        f"{system.upper()} | {inp.project_name}: {len(result.ranked)} candidate grids; best and "
        f"{N_ALTERNATES} alternates (placeholder costs)",
        "",
        f"{'':4} {'structural system':<31} {'grid':<26} {'cols':>4} {'score':>7} {'infeas':>6} "
        f"{'>depth':>6} {'depth in':>8} {'framing t':>9} {'column t':>8} {'conc cy':>8} "
        f"{'timber cf':>9} {'cost $':>11} {'$/sf':>6} {'kgCO2e':>9} {'kg/sf':>6}",
    ]
    for rank, e in enumerate(result.ranked[:1 + N_ALTERNATES]):
        lines.append(_scheme_row("best" if rank == 0 else f"alt{rank}", e))
    if not result.ranked:
        lines.append("  no candidate grid")
        return "\n".join(lines + [f"  {w}" for w in result.warnings])

    best = result.ranked[0]
    lines += [
        "",
        f"Best: x_ft={list(best.candidate.layout.grid.x_ft)}  "
        f"y_ft={list(best.candidate.layout.grid.y_ft)}; framed area {best.area_sf:,.0f} sf; "
        f"concrete {best.concrete_cy:,.0f} cy",
        "",
        "Warnings (best scheme, grouped):",
    ]
    warnings = result.warnings + best.candidate.warnings + best.design.warnings
    for message, count in _grouped(warnings):
        lines.append(f"  {count:>3} x {message}")
    return "\n".join(lines)


def _scheme_row(label: str, e: Evaluated) -> str:
    c, area = e.candidate, e.area_sf or 1.0
    return (
        f"{label:<4} {e.label[:31]:<31} {e.grid_id:<26} {c.layout.column_count:>4} "
        f"{e.rank_score:>7.3f} {c.n_infeasible:>6} {c.n_over_depth:>6} {e.max_depth_in:>8.1f} "
        f"{e.framing_tons:>9.1f} {e.column_tons:>8.1f} {e.concrete_cy:>8,.0f} "
        f"{e.timber_cf:>9,.0f} {e.cost_usd:>11,.0f} {e.cost_usd / area:>6.2f} "
        f"{e.carbon_kgco2e:>9,.0f} {e.carbon_kgco2e / area:>6.2f}"
    )


def comparison_table(inp: GravityInput, results: dict[str, SystemResult]) -> str:
    """Best scheme of each output system side by side (spec 6, prompt 8). Raw totals only:
    rank_score is not comparable across systems (spec 4.6)."""
    lines = [
        f"SYSTEM COMPARISON | {inp.project_name} (best scheme per system; placeholder costs)",
        "",
        f"{'system':<12} {'structural system':<31} {'grid':<26} {'cols':>4} {'depth in':>8} "
        f"{'steel t':>8} {'conc cy':>8} {'timber cf':>9} {'cost $':>11} {'$/sf':>6} "
        f"{'kgCO2e':>9} {'kg/sf':>6} {'infeas':>6} {'>depth':>6} {'warnings':>8}",
    ]
    for system, result in results.items():
        if not result.ranked:
            lines.append(f"{system:<12} no candidate grid")
            continue
        e = result.ranked[0]
        c, area = e.candidate, e.area_sf or 1.0
        n_warn = len(set(result.warnings + c.warnings + e.design.warnings))
        lines.append(
            f"{system:<12} {e.label[:31]:<31} {e.grid_id:<26} {c.layout.column_count:>4} "
            f"{e.max_depth_in:>8.1f} {e.framing_tons + e.column_tons:>8.1f} "
            f"{e.concrete_cy:>8,.0f} {e.timber_cf:>9,.0f} {e.cost_usd:>11,.0f} "
            f"{e.cost_usd / area:>6.2f} {e.carbon_kgco2e:>9,.0f} {e.carbon_kgco2e / area:>6.2f} "
            f"{c.n_infeasible:>6} {c.n_over_depth:>6} {n_warn:>8}"
        )
    return "\n".join(lines)


def summary_table(inp: GravityInput, system: str = "steel", top: int = 15) -> str:
    results, warnings = evaluate_all(inp, system)
    lines = [
        f"{system.upper()} candidate grids for {inp.project_name!r}: {len(results)} candidates "
        "(framing only; placeholder costs)",
        "Ranked by: infeasible bays, then bays over depth, then total framing cost. "
        "Framing-only view; the final ranking (columns, weighted score) is "
        "'python -m gravity report'",
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
    weights for its grid. ``grid_id`` defaults to the best scheme's grid."""
    if grid_id is None:
        result = optimize(inp, system)
        if not result.ranked:
            return "no candidate grid"
        zone_map = result.zone_map
        candidate, design = result.ranked[0].candidate, result.ranked[0].design
    else:
        zone_map = resolve_zones(inp)
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
    if design.material == "steel":
        totals = (f"{design.tons:.2f} tons, ${design.cost_usd:,.0f} (placeholder "
                  f"${design.cost_per_ton_usd:,.0f}/ton), {design.carbon_kgco2e:,.0f} kgCO2e "
                  f"({design.carbon_kg_per_tonne:g} kg/t x 0.9072 t/ton)")
    else:
        totals = (f"{design.concrete_cy:,.1f} cy concrete, {design.tons:.2f} tons rebar, "
                  f"{design.timber_cf:,.0f} cf glulam, ${design.cost_usd:,.0f} (placeholder "
                  f"rates), {design.carbon_kgco2e:,.0f} kgCO2e")
    lines += [
        "",
        f"All columns, grid {candidate.grid_id}: {totals}, max ratio {design.max_ratio:.3f}",
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
    """Every number for one column: tributary loads, reduction, combos, capacity."""
    k, fy = cfg.get("k", 1.0), cfg.get("fy_ksi", 0.0)
    if "splice_every_n_levels" in cfg:  # steel
        basis = (f"Fy = {fy:g} ksi, E = {cfg['e_ksi']:g} ksi, K = {k:g}, target Pu/phiPn <= "
                 f"{cfg['target_ratio']:g}, splice every {cfg['splice_every_n_levels']} stories")
    else:
        basis = f"target ratio <= {cfg['target_ratio']:g}, one size up the full height"
    lines = [
        f"Column {stack.column_id} at ({stack.x_ft:g}, {stack.y_ft:g}) ft, grid {grid_id}: "
        f"{stack.kind} column, KLL = {stack.kll:g}",
        basis,
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
        if s.shape is not None and s.capacity is not None:
            lines += [
                f"  {s.capacity.note(s.shape, k, fy)}",
                f"  ratio = Pu/phiPn = {s.pu_kips:.2f}/{s.capacity.phi_pn_kips:.1f} = {s.ratio:.3f}",
            ]
        elif s.shape is not None:
            lines += [f"  {s.capacity_note}", f"  ratio = {s.ratio:.3f}"]
    return lines


def _grouped(warnings: list[str]) -> list[tuple[str, int]]:
    """Collapse per-bay warnings that differ only in their 'LEVEL bay ID: ' prefix."""
    counts: Counter[str] = Counter()
    for w in warnings:
        head, sep, rest = w.partition(": ")
        counts[rest if sep and " bay " in head else w] += 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
