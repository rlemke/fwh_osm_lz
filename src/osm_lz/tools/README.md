# osm-lz tools

CLI helpers for submitting and inspecting workflows from this catalog.

This package has no operations of its own — it is a workflow catalog
that composes facets from
[fwh_osm](https://github.com/rlemke/fwh_osm). So unlike the per-facet
`tools/` directories in fwh_osm / fwh_noaa_weather / fwh_jenkins, the
CLIs here wrap **workflow submission**, not individual operations.

## CLIs

| CLI | Purpose |
|-----|---------|
| `list-workflows.sh` | Print the workflow catalog (qualified name + description + inputs) |
| `submit.sh` | Submit a workflow by qualified name; library FFL files are auto-resolved |

## Conventions

- `list-workflows.sh --qualified-only` prints one name per line for piping.
- `submit.sh --workflow <qualified-name> [--inputs <json>] [--task-list <name>]`.
- Both honour `FW_MONGODB_URL` / `FW_MONGODB_DATABASE` from the
  environment (set them in your shell or in Facetwork's `.env`).
- `submit.sh` is a thin wrapper around `facetwork-submit` — it adds the
  primary/library FFL paths from `_lib/workflows.py` so callers don't
  have to know which file defines which workflow.

## Examples

```bash
# See what's available
src/osm_lz/tools/list-workflows.sh
src/osm_lz/tools/list-workflows.sh --qualified-only | grep '^continental.lz'

# Submit a per-region LZ build
src/osm_lz/tools/submit.sh \
  --workflow continental.lz.BuildEuropeLowZoom \
  --inputs '{"output_base": "/data/lz-output"}'

# Submit a single transit-agency analysis
src/osm_lz/tools/submit.sh --workflow continental.transit.AnalyzeAmtrak

# Submit the full pipeline (LZ + transit, all regions, all agencies)
src/osm_lz/tools/submit.sh \
  --workflow continental.FullContinentalPipeline \
  --inputs '{"output_base": "/data/lz-output"}'
```

## Adding a new tool

1. If the tool is workflow-related (kicking off, inspecting,
   monitoring), add its catalog metadata to `_lib/workflows.py` and a
   thin CLI here.
2. If the tool wraps a real OSM operation (download, parse, build),
   it belongs in [fwh_osm](https://github.com/rlemke/fwh_osm)'s
   `tools/`, not here. This package stays handler-free on purpose.
