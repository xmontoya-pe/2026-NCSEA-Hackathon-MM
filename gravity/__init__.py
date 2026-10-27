"""Gravity optimizer: dimensioned floor plan + occupancy zones in, gravity schemes out.

Exposes ``run(input_dict) -> dict`` for the integration team (spec sections 1 and 4).

``run`` validates the input against ``models.GravityInput``, runs the spec 4.7 pipeline
(grid -> bays -> columns -> score; ``optimizer.optimize``) for every requested system
(steel, concrete, mass timber), and returns the best scheme per system with up to 3
alternates. A requested system with no configuration is skipped with a warning.
"""

from __future__ import annotations

import logging
from typing import Any

from gravity.models import GravityInput, GravityOutput

__all__ = ["run"]

logger = logging.getLogger(__name__)
# Library convention: stay silent unless the caller configures logging.
logger.addHandler(logging.NullHandler())

PLACEHOLDER_COST_WARNING = (
    "Costs use placeholder unit rates (config/costs.json); verify with an estimator "
    "before relying on cost totals or rankings"
)


def run(input_dict: dict[str, Any]) -> dict[str, Any]:
    """Validate ``input_dict`` (spec 2.1) and return an ``output.json`` dict (spec 3.1).

    Each scheme warning is also emitted on the ``gravity`` logger at WARNING level, so
    callers can route them into their own logs.

    Raises ``pydantic.ValidationError`` if the input does not match the contract.
    """
    from gravity.optimizer import is_built, optimize, schemes_for

    gravity_input = GravityInput.model_validate(input_dict)
    built = [s for s in gravity_input.systems if is_built(s)]
    skipped = [
        f"System {s!r} is not built yet; no scheme returned for it"
        for s in gravity_input.systems if s not in built
    ]

    schemes = []
    for system in built:
        result = optimize(gravity_input, system)
        if not result.ranked:
            for w in result.warnings + [f"no candidate grid for {system}; no scheme returned"]:
                logger.warning("[%s] %s: %s", gravity_input.project_name, system, w)
            continue
        schemes.append(schemes_for(gravity_input, result, skipped + [PLACEHOLDER_COST_WARNING]))
    if not schemes:
        for w in skipped:
            logger.warning("[%s] %s", gravity_input.project_name, w)

    output = GravityOutput.model_validate({"schemes": schemes}).model_dump(mode="json")
    for scheme in output["schemes"]:
        for warning in scheme["warnings"]:
            logger.warning("[%s] %s: %s", gravity_input.project_name, scheme["system"], warning)
    return output
