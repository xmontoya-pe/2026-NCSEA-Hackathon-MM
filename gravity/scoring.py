"""Cost / depth / carbon normalization and weighted score.

Spec section 4.6. Min-max normalizes cost, max depth, and carbon to 0-1 across all
candidates **within one system** and combines them with ``objective_weights`` (lower is
better). Cross-system comparison is the dashboard's job; we report raw totals.

The weights are used as given (not rescaled to sum to 1). A metric that is the same for
every candidate normalizes to 0, so it does not affect the ranking.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from gravity.models import ObjectiveWeights

FLAT_TOL = 1e-12


@dataclass(frozen=True)
class Metrics:
    """Totals of one candidate grid: framing + columns."""

    cost_usd: float
    max_depth_in: float
    carbon_kgco2e: float


def normalize(values: Sequence[float]) -> list[float]:
    """Min-max to 0-1; all zeros when the values are (nearly) equal."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < FLAT_TOL:
        return [0.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def weighted_scores(metrics: Sequence[Metrics], weights: ObjectiveWeights) -> list[float]:
    """One score per candidate, in the same order: sum of weight x normalized metric."""
    cost = normalize([m.cost_usd for m in metrics])
    depth = normalize([m.max_depth_in for m in metrics])
    carbon = normalize([m.carbon_kgco2e for m in metrics])
    return [
        weights.cost * c + weights.depth * d + weights.carbon * k
        for c, d, k in zip(cost, depth, carbon)
    ]
