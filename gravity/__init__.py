"""Gravity optimizer: dimensioned floor plan + occupancy zones in, gravity schemes out.

Exposes ``run(input_dict) -> dict`` for the integration team (spec sections 1 and 4).

STUB (spec section 6, prompt 1): ``run`` validates the input against
``models.GravityInput`` and returns a hardcoded, schema-valid steel scheme
(``stub_output.json``) so teammates can integrate now. No engineering is performed.
The pipeline in spec 4.7 (grid -> bays -> columns -> score) replaces the stub later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gravity.models import GravityInput, GravityOutput

__all__ = ["run"]

_STUB_OUTPUT = Path(__file__).with_name("stub_output.json")


def run(input_dict: dict[str, Any]) -> dict[str, Any]:
    """Validate ``input_dict`` (spec 2.1) and return an ``output.json`` dict (spec 3.1).

    Raises ``pydantic.ValidationError`` if the input does not match the contract.
    """
    GravityInput.model_validate(input_dict)

    canned = json.loads(_STUB_OUTPUT.read_text(encoding="utf-8"))
    return GravityOutput.model_validate(canned).model_dump(mode="json")
