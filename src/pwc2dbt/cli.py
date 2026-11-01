"""Command line: ``python -m pwc2dbt --input X.xml --output DIR (--mapping NAME | --all-mappings)``.

Exit codes: 0 every model complete; 2 output written but at least one model
INCOMPLETE; 1 fatal (unreadable XML, invalid mapping graph, unknown mapping,
refusal to overwrite). When 1 and 2 both apply, 1 wins.
"""

import argparse
import sys
from pathlib import Path

from .compiler import INCOMPLETE, compile_folder
from .dbt_project import OutputError, write_project
from .parser import ParseError, parse_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pwc2dbt", description="Convert PowerCenter mapping XML into dbt models.")
    parser.add_argument("--input", required=True, type=Path, help="PowerCenter repository export (XML)")
    parser.add_argument("--output", required=True, type=Path, help="directory for the generated dbt project")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--mapping", help="convert only this mapping")
    which.add_argument("--all-mappings", action="store_true", help="convert every mapping in the folder")
    parser.add_argument("--overwrite", action="store_true", help="replace models/ and incomplete/ in a non-empty output")
    args = parser.parse_args(argv)

    try:
        folder = parse_file(args.input)
    except ParseError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    available = [m.name for m in folder.mappings]
    if args.mapping and args.mapping not in available:
        print(f"ERROR unknown mapping {args.mapping}; available: {', '.join(available)}", file=sys.stderr)
        return 1

    results, diagnostics = compile_folder(folder, [args.mapping] if args.mapping else None)
    try:
        write_project(results, folder, args.output, args.overwrite)
    except OutputError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    for mapping in (m for m in available if not args.mapping or m == args.mapping):
        print(f"Mapping {mapping}")
        for r in (r for r in results if r.mapping == mapping):
            print(f"  {r.name}: {r.status}")
            for d in r.diagnostics:
                if d.severity == INCOMPLETE:
                    print(f"    {d.message}")
    all_diagnostics = diagnostics + [d for r in results for d in r.diagnostics]
    for d in all_diagnostics:
        print(str(d))

    errors = sum(d.severity == "ERROR" for d in all_diagnostics)
    incomplete = sum(r.status == INCOMPLETE for r in results)
    print(
        f"Summary: {len(results) - incomplete} complete, {incomplete} incomplete, {errors} errors, "
        f"{sum(d.severity == 'WARNING' for d in all_diagnostics)} warnings; output in {args.output}"
    )
    return 1 if errors else 2 if incomplete else 0
