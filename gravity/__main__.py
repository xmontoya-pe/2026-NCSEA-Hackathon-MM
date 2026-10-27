"""CLI: ``python -m gravity run <input.json> -o <output_dir>`` (spec section 1).

Reads ``input.json`` (spec 2.1), calls ``gravity.run``, writes
``<output_dir>/output.json`` (spec 3.1) and ``<output_dir>/warnings.log``, and reports
whether the log contains warnings. Exits with status 2 if the input does not match
the contract in ``gravity/models.py``. Warnings alone do not change the exit status.

``python -m gravity schema -o <dir>`` writes ``input.schema.json`` and
``output.schema.json`` (JSON Schema generated from ``gravity/models.py``) for the
integration team.

``python -m gravity summary <input.json>`` prints the ranked steel candidate grids and
the bay framing of the best one (spec 6, prompt 5).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from gravity import run
from gravity.exporters import write_output, write_schemas, write_warnings_log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gravity")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="optimize gravity schemes for an input.json")
    run_parser.add_argument("input", type=Path, help="path to input.json")
    run_parser.add_argument(
        "-o", "--output-dir", type=Path, default=Path("."), help="directory for output.json and warnings.log"
    )

    schema_parser = sub.add_parser("schema", help="write JSON Schema files for input and output")
    schema_parser.add_argument(
        "-o", "--output-dir", type=Path, default=Path("."), help="directory for the schema files"
    )

    summary_parser = sub.add_parser(
        "summary", help="print the ranked candidate grids and best-grid framing (steel)"
    )
    summary_parser.add_argument("input", type=Path, help="path to input.json")
    summary_parser.add_argument("--top", type=int, default=15, help="candidates to list")

    args = parser.parse_args(argv)

    if args.command == "schema":
        for path in write_schemas(args.output_dir):
            print(f"wrote {path}")
        return 0

    if args.command == "summary":
        from gravity.models import GravityInput
        from gravity.report import summary_table

        try:
            inp = GravityInput.model_validate_json(args.input.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            print(f"error: cannot use {args.input}: {exc}", file=sys.stderr)
            return 2
        print(summary_table(inp, top=args.top))
        return 0

    try:
        input_dict = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {args.input}: {exc}", file=sys.stderr)
        return 2

    try:
        result = run(input_dict)
    except ValidationError as exc:
        print(f"error: {args.input} does not match the input contract:\n{exc}", file=sys.stderr)
        return 2

    out_path = write_output(result, args.output_dir)
    log_path, warnings = write_warnings_log(result, args.output_dir, input_dict["project_name"])

    print(f"wrote {out_path} ({len(result['schemes'])} scheme(s))")
    if warnings:
        print(f"WARNINGS: {len(warnings)} warning(s) logged to {log_path}")
        for system, warning in warnings:
            print(f"  [{system}] {warning}")
    else:
        print(f"no warnings (log: {log_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
