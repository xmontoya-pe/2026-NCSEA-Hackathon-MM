"""CLI: ``python -m gravity run <input.json> -o <output_dir>`` (spec section 1).

Reads ``input.json`` (spec 2.1), calls ``gravity.run``, writes
``<output_dir>/output.json`` (spec 3.1) and ``<output_dir>/warnings.log``, and reports
whether the log contains warnings. Exits with status 2 if the input does not match
the contract in ``gravity/models.py``. Warnings alone do not change the exit status.

``python -m gravity schema -o <dir>`` writes ``input.schema.json`` and
``output.schema.json`` (JSON Schema generated from ``gravity/models.py``) for the
integration team.

``python -m gravity examples`` regenerates ``examples/sample_output.json`` (``run`` on
``examples/sample_input.json``) and the schema files.

``python -m gravity dbdump [-o out/db_dump_p8.txt]`` writes the concrete and mass timber
bay study tables to a text file for review (spec 6, prompt 8).

``python -m gravity report <input.json> [<input.json> ...]`` prints, per input, the best
steel scheme and its alternates: grid, max depth, steel tons (beams/girders vs columns),
cost, carbon and warnings (spec 6, prompt 7).

``python -m gravity summary <input.json>`` prints the steel candidate grids ranked by
framing alone and the bay framing of the first one (spec 6, prompt 5).

``python -m gravity column <input.json> --column B-2 [--grid "X:30x5 Y:30x3"]`` prints
the hand-check trace for one steel column, plus column totals and seismic weights
(spec 6, prompt 6).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from gravity import run
from gravity.exporters import write_examples, write_output, write_schemas, write_warnings_log

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


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

    sub.add_parser("examples", help="regenerate examples/sample_output.json and the schemas")

    dbdump_parser = sub.add_parser(
        "dbdump", help="write the non-steel bay study tables to a text file for review"
    )
    dbdump_parser.add_argument("-o", "--output", type=Path, default=Path("out/db_dump_p8.txt"),
                               help="file to write (default: out/db_dump_p8.txt)")

    report_parser = sub.add_parser(
        "report", help="best steel scheme and alternates per input: tons, cost, carbon, warnings"
    )
    report_parser.add_argument("inputs", type=Path, nargs="+", help="input.json file(s)")

    summary_parser = sub.add_parser(
        "summary", help="print candidate grids ranked by framing alone (steel)"
    )
    summary_parser.add_argument("input", type=Path, help="path to input.json")
    summary_parser.add_argument("--top", type=int, default=15, help="candidates to list")

    column_parser = sub.add_parser(
        "column", help="hand-check trace for one steel column (every number)"
    )
    column_parser.add_argument("input", type=Path, help="path to input.json")
    column_parser.add_argument("--column", required=True, help="column id, e.g. B-2")
    column_parser.add_argument("--grid", default=None, help='grid id, e.g. "X:30x5 Y:30x3" '
                               "(default: best-ranked grid)")

    args = parser.parse_args(argv)

    if args.command == "column":
        from gravity.models import GravityInput
        from gravity.report import column_report

        try:
            inp = GravityInput.model_validate_json(args.input.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            print(f"error: cannot use {args.input}: {exc}", file=sys.stderr)
            return 2
        print(column_report(inp, args.column, args.grid))
        return 0

    if args.command == "dbdump":
        from gravity.db_adapter import review_dump

        text = review_dump()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"wrote {args.output.resolve()} ({len(text.splitlines())} lines)")
        return 0

    if args.command == "examples":
        for path in write_examples(EXAMPLES_DIR):
            print(f"wrote {path}")
        return 0

    if args.command == "report":
        from gravity.models import GravityInput
        from gravity.report import scheme_report

        status = 0
        for path in args.inputs:
            try:
                inp = GravityInput.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as exc:
                print(f"error: cannot use {path}: {exc}", file=sys.stderr)
                status = 2
                continue
            print(f"=== {path}")
            print(scheme_report(inp))
            print()
        return status

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
