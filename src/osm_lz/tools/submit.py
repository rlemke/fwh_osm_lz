#!/usr/bin/env python3
"""submit — submit an osm-lz workflow to a Facetwork runner.

Resolves the ``--primary`` and ``--library`` FFL files for the workflow
automatically from the catalog so callers don't have to list them. The
runner this submits to is whatever ``FW_MONGODB_URL`` /
``FW_MONGODB_DATABASE`` point at — set them in your shell or in
Facetwork's ``.env``.

This is a thin wrapper around ``facetwork-submit`` (which lives in the
facetwork package) — the only value-add is path resolution from the
catalog.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

from osm_lz.tools._lib.workflows import CATALOG, find, library_files


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument(
        "--workflow",
        required=True,
        help="Qualified workflow name (use list-workflows.sh to see the catalog)",
    )
    p.add_argument(
        "--inputs",
        default="{}",
        help="JSON string of input parameters (default: empty object)",
    )
    p.add_argument(
        "--task-list",
        default="default",
        help="Task list name (default: default)",
    )
    args = p.parse_args()

    info = find(args.workflow)
    if info is None:
        print(f"submit: unknown workflow '{args.workflow}'", file=sys.stderr)
        names = "\n  ".join(w.qualified_name for w in CATALOG)
        print(f"available workflows:\n  {names}", file=sys.stderr)
        return 2

    # Validate the inputs JSON early so a typo doesn't reach mongo.
    try:
        json.loads(args.inputs)
    except json.JSONDecodeError as e:
        print(f"submit: --inputs is not valid JSON: {e}", file=sys.stderr)
        return 2

    cli = shutil.which("facetwork-submit")
    if cli is None:
        print(
            "submit: 'facetwork-submit' not on PATH — install facetwork (e.g. pip install facetwork) or activate the venv",
            file=sys.stderr,
        )
        return 127

    cmd = [
        cli,
        "--primary", str(info.primary),
        "--workflow", info.qualified_name,
        "--inputs", args.inputs,
        "--task-list", args.task_list,
        "--log-format", "text",
    ]
    for lib in library_files(info.primary):
        cmd.extend(["--library", str(lib)])

    print(f"submit: {info.qualified_name}", file=sys.stderr)
    print(f"  primary: {info.primary.name}", file=sys.stderr)
    print(f"  library: {', '.join(p.name for p in library_files(info.primary))}", file=sys.stderr)
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
