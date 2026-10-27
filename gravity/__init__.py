"""Gravity optimizer: dimensioned floor plan + occupancy zones in, gravity schemes out.

Exposes ``run(input_dict) -> dict`` for the integration team (spec sections 1 and 4).

STUB (spec section 6, prompt 1): ``run`` validates the input against
``models.GravityInput`` and returns a hardcoded, schema-valid steel scheme
(``stub_output.json``) so teammates can integrate now. No engineering is performed.
The pipeline in spec 4.7 (grid -> bays -> columns -> score) replaces the stub later.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from gravity.models import GravityInput, GravityOutput

__all__ = ["run"]

logger = logging.getLogger(__name__)
# Library convention: stay silent unless the caller configures logging.
logger.addHandler(logging.NullHandler())

_STUB_OUTPUT = Path(__file__).with_name("stub_output.json")


def run(input_dict: dict[str, Any]) -> dict[str, Any]:
    """Validate ``input_dict`` (spec 2.1) and return an ``output.json`` dict (spec 3.1).

    Each scheme warning is also emitted on the ``gravity`` logger at WARNING level, so
    callers can route them into their own logs.

    Raises ``pydantic.ValidationError`` if the input does not match the contract.
    """
    gravity_input = GravityInput.model_validate(input_dict)

    canned = json.loads(_STUB_OUTPUT.read_text(encoding="utf-8"))
    result = GravityOutput.model_validate(canned).model_dump(mode="json")

    for scheme in result["schemes"]:
        for warning in scheme["warnings"]:
            logger.warning("[%s] %s: %s", gravity_input.project_name, scheme["system"], warning)
    return result
