"""Cost/depth/carbon normalization and weighted score within one system (spec 4.6).

docs/ASSUMPTIONS.md S-*. Lower is better.

- Each metric (total cost, max structure depth, total carbon; framing + columns) is
  min-max normalized to 0..1 across every candidate of one system.
- score = sum(weight x normalized metric) / sum(weights), so 0 <= score <= 1.
- rank_score = score + penalties (``config/systems.json`` ``scoring``): per bay over the
  allowable depth, 1.0 plus 1.0 per inch over; per infeasible bay, 1000. Every penalty
  is at least 1, larger than any score difference, so a penalized candidate always ranks
  after a clean one (spec 4.3, 4.4: penalize, never remove), and deeper overruns rank
  after shallower ones.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from gravity.models import ObjectiveWeights

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
METRICS = ("cost", "depth", "carbon")


def weighted_scores(metrics: list[dict[str, float]], weights: ObjectiveWeights) -> list[float]:
    """One 0..1 score per candidate; ``metrics`` rows have keys cost, depth, carbon."""
    total_weight = sum(getattr(weights, m) for m in METRICS)
    if not metrics or total_weight <= 0:
        return [0.0] * len(metrics)
    ranges = {m: (min(r[m] for r in metrics), max(r[m] for r in metrics)) for m in METRICS}

    def norm(m: str, value: float) -> float:
        lo, hi = ranges[m]
        return 0.0 if hi - lo < 1e-12 else (value - lo) / (hi - lo)

    return [
        sum(getattr(weights, m) * norm(m, r[m]) for m in METRICS) / total_weight
        for r in metrics
    ]


def penalty(n_infeasible: int, n_over_depth: int, over_depth_in: float = 0.0) -> float:
    """``over_depth_in`` is the sum over bays of the inches each exceeds its allowable."""
    cfg = _scoring_config()
    return (n_infeasible * cfg["infeasible_bay_penalty"]
            + n_over_depth * cfg["over_depth_bay_penalty"]
            + over_depth_in * cfg["over_depth_penalty_per_in"])


@lru_cache(maxsize=None)
def _scoring_config() -> dict[str, Any]:
    return json.loads((CONFIG_DIR / "systems.json").read_text(encoding="utf-8"))["scoring"]
