"""Output writers.

Spec sections 3.1 and 3.2. Writes ``output.json`` from ``models.GravityOutput``, a
plain-text ``warnings.log`` collecting every scheme's ``warnings`` for investigation,
and JSON Schema files for the input and output contracts (for the integration team).

Planned: an optional lateral-team format (grid lines and column locations, seismic
weight and center of mass per level, diaphragm type, gravity column axial loads) once
the lateral team says what they want. Other formats are added here as exporters; the
core model in ``models.py`` does not change for them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gravity.models import GravityInput, GravityOutput


def write_output(result: dict[str, Any], output_dir: Path) -> Path:
    """Write ``result`` (spec 3.1) to ``<output_dir>/output.json``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "output.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return path


def schemas() -> dict[str, dict[str, Any]]:
    """JSON Schema for the input and output contracts, generated from ``models.py``."""
    return {
        "input.schema.json": GravityInput.model_json_schema(),
        "output.schema.json": GravityOutput.model_json_schema(),
    }


def write_schemas(output_dir: Path) -> list[Path]:
    """Write ``input.schema.json`` and ``output.schema.json`` to ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, schema in schemas().items():
        path = output_dir / name
        path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def collect_warnings(result: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ``(system, warning)`` pairs from every scheme in ``result``."""
    return [(s["system"], w) for s in result["schemes"] for w in s["warnings"]]


def write_warnings_log(
    result: dict[str, Any], output_dir: Path, project_name: str
) -> tuple[Path, list[tuple[str, str]]]:
    """Write ``<output_dir>/warnings.log``, one ``[system] warning`` line per warning.

    The file is rewritten on every run, including runs with no warnings, so a stale
    log from an earlier run is never mistaken for the current one.
    """
    warnings = collect_warnings(result)
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# gravity warnings log | project: {project_name} | generated: {stamp}"
        f" | {len(warnings)} warning(s)",
        *(f"[{system}] {warning}" for system, warning in warnings),
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "warnings.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, warnings
