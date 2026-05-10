# CLAUDE.md — osm-lz

This repository is a **standalone Facetwork example package**, paired
with [fwh_osm](https://github.com/rlemke/fwh_osm). The Facetwork
platform (workflow compiler + runtime) lives at
`/Users/ralph_lemke/facetwork`; this repo only ships FFL workflow
definitions for the OSM Low-Zoom (LZ) road infrastructure pipeline and
GTFS transit analysis. The handlers it depends on live in fwh_osm.

## Quick orientation

```
fwh_osm_lz/
├── pyproject.toml                 # declares the facetwork.examples entry point
├── src/osm_lz/__init__.py         # exports `example: ExamplePackage` (no-op register_handlers)
├── src/osm_lz/ffl/                # 4 FFL files (no Python handlers)
├── src/osm_lz/tools/              # workflow-submission CLIs (the example's surface)
└── agent-spec/                    # cross-cutting design specs
```

## How this package differs from fwh_osm / fwh_noaa_weather / fwh_jenkins

| | fwh_osm | fwh_noaa_weather | fwh_jenkins | **fwh_osm_lz** |
|---|---|---|---|---|
| Defines event facets | yes | yes | yes | **no** |
| Defines handlers | yes (132+) | yes (13) | yes (17) | **no** |
| Defines FFL workflows | yes | yes | yes | yes (20) |
| `tools/_lib/` | per-domain simulators / real impls | same | per-domain simulators | **workflow catalog metadata** |
| `tools/*.sh` | one CLI per facet | one CLI per facet | one CLI per facet | **submit / list-workflows** |
| Depends on another fwh package | no | no | no | **fwh_osm** |

osm-lz is the canonical demo of **pure FFL composition** — workflows
that orchestrate facets defined elsewhere. The `tools/` here can't wrap
"the operations of a facet" because there are no facets to wrap; it
wraps "submitting a workflow this catalog defines" instead.

## Common operations

```bash
# Install the handler package (required) + this catalog
pip install -e ~/fw_handlers/fwh_osm
pip install -e ~/fw_handlers/fwh_osm_lz

# From a Facetwork checkout:
scripts/seed-examples --include osm-lz
scripts/start-runner --example osm-geocoder --example osm-lz -- --log-format text

# CLIs
src/osm_lz/tools/list-workflows.sh
src/osm_lz/tools/submit.sh --workflow continental.lz.BuildEuropeLowZoom \
                          --inputs '{"output_base": "/data/lz-output"}'
```

## Naming choices

The example name is `osm-lz` — it describes which handler family these
workflows compose against (osm-*). The internal FFL namespaces are
`continental.*` (`continental.lz`, `continental.transit`,
`continental.types`) — they describe the multi-country scope of the
workflows themselves. The two are intentionally decoupled: the example
identifies who provides the handlers; the namespace identifies what
the workflows do.

If you add new workflows that aren't continental-scope, give them a
sibling top-level namespace (e.g. `regional.lz`) — don't graft new
workflows under `continental.*` if their region story is different.

## Adding a new workflow

1. Add the workflow declaration to the right FFL file under
   `src/osm_lz/ffl/` (or create a new file in the same dir; the
   discovery layer rglobs `*.ffl`).
2. Add a row to `tools/_lib/workflows.py` so `tools/list-workflows.sh`
   surfaces it and `tools/submit.sh` can find it without `--library`
   flags.
3. Re-run `scripts/seed-examples --include osm-lz` so the new workflow
   shows up in the dashboard.

## Code review checklist

- All workflow facets must resolve in one of the namespaces fwh_osm
  ships (`osm.cache.*`, `osm.cache.GraphHopper.*`, `osm.Roads.*`,
  `osm.Transit.GTFS.*`). If you reference a new osm namespace, add the
  handler in fwh_osm first; don't define event facets in this repo.
- For every new workflow: verify it compiles via `afl <file> --check`
  with the other FFL files passed as `--library`.
- Keep the workflow inputs simple — `String`, `Long`, etc. The
  `tools/submit.sh` helper assumes inputs serialize cleanly to JSON.
- Don't add `pymongo` or `requests` deps here. This package has no
  Python operations beyond the metadata catalog.
