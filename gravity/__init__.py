"""Gravity optimizer: dimensioned floor plan + occupancy zones in, gravity schemes out.

Exposes ``run(input_dict) -> dict`` for the integration team (spec sections 1 and 4).

``run`` validates the input against ``models.GravityInput`` and runs the spec 4.7
pipeline (grid -> bays -> columns -> score, ``optimizer.optimize``) for each requested
system. Only steel is built; a requested system that is not built yet gets no scheme,
and every returned scheme carries a warning saying so. ``stub_output.json`` is the old
canned output, kept for reference.
"""

from __future__ import annotations

import logging
from typing import Any

from gravity.models import GravityInput, GravityOutput

__all__ = ["run"]

logger = logging.getLogger(__name__)
# Library convention: stay silent unless the caller configures logging.
logger.addHandler(logging.NullHandler())


def run(input_dict: dict[str, Any]) -> dict[str, Any]:
    """Validate ``input_dict`` (spec 2.1) and return an ``output.json`` dict (spec 3.1).

    Each scheme warning is also emitted on the ``gravity`` logger at WARNING level, so
    callers can route them into their own logs.

    Raises ``pydantic.ValidationError`` if the input does not match the contract.
    """
    from gravity.optimizer import BUILT_SYSTEMS, optimize

    gravity_input = GravityInput.model_validate(input_dict)

    requested = list(dict.fromkeys(gravity_input.systems))
    not_built = [s for s in requested if s not in BUILT_SYSTEMS]
    schemes = [optimize(gravity_input, s) for s in requested if s in BUILT_SYSTEMS]
    for scheme in schemes:
        scheme["warnings"].extend(
            f"System {s!r} was requested but is not built yet; no scheme returned for it"
            for s in not_built
        )
    if not schemes and not_built:
        logger.warning("[%s] no built system requested (%s)", gravity_input.project_name,
                       ", ".join(not_built))
    result = GravityOutput.model_validate({"schemes": schemes}).model_dump(mode="json")

    for scheme in result["schemes"]:
        for warning in scheme["warnings"]:
            logger.warning("[%s] %s: %s", gravity_input.project_name, scheme["system"], warning)
    return result
