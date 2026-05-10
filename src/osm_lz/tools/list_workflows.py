#!/usr/bin/env python3
"""list-workflows — print the osm-lz workflow catalog.

Output format:

    continental.lz.BuildEuropeLowZoom
        LZ pipeline for the 12 European regions (DE, FR, UK, ES, IT, …)
        primary: continental_lz_workflows.ffl
        inputs:  output_base: String, default '/data/lz-output'

Use ``--qualified-only`` for a flat newline-separated list — handy for
piping into a shell loop.
"""

from __future__ import annotations

import argparse
import sys

from osm_lz.tools._lib.workflows import CATALOG


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument(
        "--qualified-only",
        action="store_true",
        help="Print only qualified names, one per line (machine-readable)",
    )
    args = p.parse_args()

    if args.qualified_only:
        for w in CATALOG:
            print(w.qualified_name)
        return 0

    print(f"osm-lz workflow catalog ({len(CATALOG)} workflows)\n")
    for w in CATALOG:
        print(w.qualified_name)
        print(f"    {w.description}")
        print(f"    primary: {w.primary.name}")
        if w.inputs:
            inputs = ", ".join(f"{k}: {v}" for k, v in w.inputs.items())
            print(f"    inputs:  {inputs}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
