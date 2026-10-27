"""CLI: ``python -m gravity run <input.json> -o <output_dir>`` (spec section 1).

Reads ``input.json`` (spec 2.1), calls ``gravity.run``, and writes
``<output_dir>/output.json`` (spec 3.1). Exits with status 2 if the input
does not match the contract in ``gravity/models.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from gravity import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gravity")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="optimize gravity schemes for an input.json")
    run_parser.add_argument("input", type=Path, help="path to input.json")
    run_parser.add_argument(
        "-o", "--output-dir", type=Path, default=Path("."), help="directory for output.json"
    )

    args = parser.parse_args(argv)

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

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "output.json"
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    n_warnings = sum(len(s["warnings"]) for s in result["schemes"])
    print(f"wrote {out_path} ({len(result['schemes'])} scheme(s), {n_warnings} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
