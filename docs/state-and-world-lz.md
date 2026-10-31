# Per-Region Low-Zoom Maps and the World Run

**Namespace:** `continental.lz.states` ·
**FFL:** `src/osm_lz/ffl/us_states_lz.ffl`, `src/osm_lz/ffl/us_states_retile.ffl` ·
**Catalog:** `src/osm_lz/catalog.yaml` (4 entries; not in `tools/_lib/workflows.py`) ·
**Driver:** `tools/lz_world.py`, `tools/lz_merge.py`, `src/osm_lz/world_{plan,merge,publish,retile}.py` ·
**Composes (fwh_osm):** `osm.ops.CacheRegion`, `osm.ops.GraphHopper.BuildGraph`,
`osm.Roads.ZoomBuilder.{BuildZoomLayers,PlanZoomRouting,RouteZoomChunks,DetectZoomChunks}`,
`osm.Tiles.BuildVectorTiles`, `osm.viz.RenderTiledMap`

## Overview

A graded low-zoom road map for ONE region per run (a US state, a country, or a
sub-region of a big country), published as streamed vector tiles: one tileset per
zoom band, so the lower road classes appear only as you zoom in. As with the rest
of this package, the workflows are pure FFL; every step runs on fwh_osm's handlers.

One region per run is a constraint, not a simplification. The builder scores roads
by ROUTING sampled city pairs, and a graphhopper-web server holds exactly one
region. A fan-out over several states against one server would get "out of bounds"
for every pair outside it, score zero betweenness everywhere and publish hollow
maps that look correct (measured 2026-09-24). The builder's router probe now
refuses to start when the server holds the wrong region.

## Workflows

| Workflow | What it does |
|----------|--------------|
| `BuildStateGraphs(states, profile, width = 1)` | Builds (or reuses) the routing graph for each named extract; `foreach … limit $.width`, serial by default because an import peaks well above the extract's size. |
| `BuildStateLowZoomMap(region, slug, label, …, memory_gb = 0)` | Extract → graph → `BuildZoomLayers` → six tilesets (z2–z7) → viewer. `memory_gb` is a `Requires` floor on the zoom builder (0 = none). |
| `BuildCountryLowZoomMap(region, slug, label, …, max_pairs = 8000, chunk_pairs = 500, route_width = 8, detect_pairs = 1500)` | A country's top bands (z2–z4) over a major-roads extract. City pairs are routed, and bypasses/rings detected, as chunk tasks across osm hosts, each through the region's own router (`fw fleet router add <region>`) when one is ready. `output_base` is relative so the layers land in the shared store. |
| `RetileStateMap(layer_dir, slug, label, basemap)` | Rebuilds the tiles + viewer from existing layer files, no re-routing. |

`slug` (path-safe) and `label` (page title) are separate on purpose, and the slug is
in every tile layer name: without it every state wrote the same PMTiles names and
overwrote the last.

## The world run (`tools/lz_world.py`)

`world_plan.py` states the rule: each continent is split into its countries; a
country at or under `--max-mb` (400) is one map; a bigger one is split into its
sub-regions, recursively, and a region with no sub-regions in the store yet is cut
first (`osm.planet.BuildAdminSetWorkflow`). A region that already has a finished map
is a leaf whatever its size. Continents run smallest first.

Per continent the driver cuts, builds every leaf's graph, then runs up to
`FW_LZ_MAPS_IN_FLIGHT` (4) maps at once, each behind its own region router, and
publishes each viewer to `cache/osm/maps/lz/<key>/` (`world_publish.py`, which also
injects an "About this map" panel into the page). It writes
`cache/osm/maps/lz/_status.json`, which the gallery's world index renders, so
planned, in-flight and failed regions are visible rather than silently missing.

It only sequences submitted workflows: a router is fleet configuration, which no FFL
step can express. Progress is in a ledger under `FW_LZ_STATE_DIR` (default
`~/.facetwork/lz-world`) and finished maps are detected in the store, so the driver
can be killed and restarted. Other knobs: `FW_LZ_MAX_MEMORY_GB`,
`FW_LZ_ROUTER_REPLICAS`, `FW_LZ_ROUTER_GROUP`, `FW_LZ_OUTPUT_BASE`,
`FW_LZ_HARD_WAIT_HOURS`.

```bash
python tools/lz_world.py --dry                       # the plan, with estimates
python tools/lz_world.py                             # every continent
python tools/lz_world.py --continents central-america
```

## Merging and re-tiling

`tools/lz_merge.py` (logic in `world_merge.py`) merges finished region maps into one
map without routing anything. What a merge is NOT, and the page says so: importance
stays per region (a sparse state's z2 is its own top-city routes), routes stop at
region borders, and regions built under different rules stay different until
rebuilt. Exact duplicate border roads are dropped; partial overlaps are drawn twice.

`world_retile.py` re-tiles maps built before the zoom export wrote `routed`, so the
viewer can split each band into routed / name-kind roads. It refuses a band whose
local layer does not hold exactly the published feature count.

## Gotchas & notes

- `submit.sh` / `list-workflows.sh` do not know these workflows (their catalog lists
  only the four `continental_*.ffl` files). Use the dashboard, `fw ffl run`, or the
  driver.
- `tools/lz_merge.py` and `tools/lz_world.py` import `boto3`, which is not in this
  package's declared dependencies; run them from the Facetwork venv.

## Related specs

- [continental-lz](continental-lz.md) — the multi-region continental workflows.
- [catalog-and-tooling](catalog-and-tooling.md) — how the package registers.
