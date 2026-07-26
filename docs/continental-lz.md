# Continental Low-Zoom (LZ) Road Infrastructure

**Namespace:** `continental.lz` ·
**FFL:** `src/osm_lz/ffl/continental_lz_workflows.ffl` ·
**Types:** `src/osm_lz/ffl/continental_types.ffl` (`RegionLZResult`, `ContinentalLZSummary`) ·
**Catalog:** `src/osm_lz/catalog.yaml` (4 entries) / `src/osm_lz/tools/_lib/workflows.py` ·
**Composes (fwh_osm):** `osm.cache.NorthAmerica.*`, `osm.cache.Europe.*`,
`osm.cache.GraphHopper.NorthAmerica.*`, `osm.cache.GraphHopper.Europe.*`,
`osm.Roads.ZoomBuilder.BuildZoomLayers`

## Overview

This is the **flagship** feature of osm-lz: it builds **low-zoom (zoom 2–7) road
tile layers** — the thinned, population-weighted road network you show on a
zoomed-out map — for the entire United States, all of Canada, and 12 European
countries. It answers "give me a continental low-zoom road map of North America +
Europe" as a single dashboard-visible run.

Every workflow here is **pure FFL composition**. osm-lz contributes zero handlers
(`src/osm_lz/__init__.py` wires a no-op `_register_no_handlers` into its
`DomainPackage`); all compute is done by the upstream `fwh_osm` event facets these
workflows dispatch to. The catalog is organised bottom-up: three per-region
builders (`BuildUSLowZoom`, `BuildCanadaLowZoom`, `BuildEuropeLowZoom`) and one
orchestrator (`BuildContinentalLZ`) that calls all three.

## How it works

Each region follows the same three-step chain, visible in the FFL as three steps
in one `andThen` block:

1. **OSM cache** — `osm.cache.<Continent>.<Country>()` returns an `OSMCache`.
   These are upstream **pure composition facets** (`facet … andThen`) that wrap the
   `osm.ops.CacheRegion(region = "north-america/us")` **event facet**
   (Effect `external` / Cost `expensive`) — i.e. download + cache the regional PBF
   from the (self-hosted-Geofabrik) mirror.
2. **Routing graph** — `osm.cache.GraphHopper.<Continent>.<Country>(cache = …)`
   returns a `GraphHopperCache`. Also an upstream pure composition facet, wrapping
   `osm.ops.GraphHopper.BuildGraph(cache, profile = "car", recreate = false)`
   (event facet, Effect `external` / Cost `expensive`) — bake a GraphHopper routing
   graph from the PBF.
3. **Zoom layers** — `osm.Roads.ZoomBuilder.BuildZoomLayers(cache, graph,
   min_population, output_dir)` runs the LZ selection algorithm and writes the
   layered road output. This is the single **event facet** (Effect `pure` / Cost
   `moderate`) that actually produces the LZ artifact; osm-lz binds its `result`
   into a `continental.types.RegionLZResult`.

Data shape per region: `region key → OSMCache (PBF) → GraphHopperCache (graph) →
zoom-layer output dir`. `BuildContinentalLZ` composes at a higher level — it calls
the three region workflows as steps and rolls their results into a
`ContinentalLZSummary`.

## Fan-out

**Fans out two ways, both via parallel sibling steps in a single `andThen` (no
`foreach` — the region set is fixed and small):**

- **Within `BuildEuropeLowZoom`** — the 12 countries are laid out as parallel
  step groups: 12 `*_osm` cache steps, then 12 `*_gh` graph steps, then 12 `*_lz`
  zoom builds (Germany, France, UK, Spain, Italy, Poland, Netherlands, Belgium,
  Switzerland, Austria, Sweden, Norway). Because independent steps in one block
  have no data dependency between countries, the runtime dispatches them as
  concurrent distributed tasks — one heavy PBF/graph/LZ chain per country runs on
  whichever fleet runner claims it.
- **Across `BuildContinentalLZ`** — `us`, `canada`, and `europe` are three
  independent nested-workflow steps, so the US chain, the Canada chain, and the
  whole 12-country Europe fan-out proceed in parallel.

This is why the README quotes ~12–30 h wall-clock for ~28 GB of PBF + ~44 GB of
graphs across 14 regions rather than the sum of the per-region estimates. The
fan-out unit is **per-country**; the fleet parallelism and per-task recovery are
owned by the Facetwork runtime, not this catalog.

## Data & fields

Schemas declared in `continental.types` and used here (see
[shared-types](shared-types.md) for the full field list):

- `RegionLZResult { region, total_edges: Long, selected_edges: Long, output_dir }`
  — the per-region LZ summary the region workflows return.
- `ContinentalLZSummary { us_total_edges, us_selected_edges, canada_*, europe_*,
  total_regions: Long }` — the roll-up `BuildContinentalLZ` returns.

Upstream types bound: `OSMCache`, `GraphHopperCache`, and the ZoomBuilder result
(`osm.Roads.ZoomBuilder.ZoomBuilderResult`), all defined in fwh_osm.

Tuning constants passed to the upstream facets (these are the only real "fields"
osm-lz sets):

- `min_population` — the LZ population threshold, tuned per region: US 50 000,
  Canada 30 000, large EU 40 000, Poland/Netherlands 30 000, small EU
  (Belgium/Switzerland/Austria/Sweden/Norway) 20 000.
- `output_dir` — derived from the `output_base` workflow input
  (`$.output_base ++ "/us"`, `"/europe/germany"`, …).
- GraphHopper `profile` defaults to `"car"` (osm-lz does not override it).

## External libraries / binaries

**None local.** osm-lz's only declared deps are `facetwork>=0.31.0` and
`PyYAML>=6.0` (`pyproject.toml`); it ships no Python operations. The heavy
dependencies used at runtime — osmium/PBF handling, **GraphHopper (Java)** for graph
baking, and the ZoomBuilder implementation — all live in the upstream `fwh_osm`
handlers this feature composes. See fwh_osm's `roads` / `graphhopper` / `cache`
feature specs for those dependencies.

## Facets & workflows

Workflows defined here (all take `output_base: String = "/data/lz-output"`):

| Workflow | Returns | Purpose (from FFL docstring) |
|---|---|---|
| `BuildUSLowZoom` | `RegionLZResult` | Low-zoom road layers for the entire United States. |
| `BuildCanadaLowZoom` | `RegionLZResult` | Low-zoom road layers for all of Canada. |
| `BuildEuropeLowZoom` | `[RegionLZResult]` | Low-zoom road layers for 12 European countries in parallel. |
| `BuildContinentalLZ` | `ContinentalLZSummary` | Orchestrates LZ builds across all three continental regions in parallel. |

Upstream `fwh_osm` facets composed (osm-lz declares **no facets of its own**):

| Facet | Kind | Effect/Cost | Role in this feature |
|---|---|---|---|
| `osm.cache.<Continent>.<Country>()` | pure composition | (wraps `external`/`expensive` `CacheRegion`) | Download/cache the regional PBF → `OSMCache`. |
| `osm.ops.CacheRegion(region)` | event | external / expensive | The actual PBF download (called by the cache facets). |
| `osm.cache.GraphHopper.<Continent>.<Country>(cache, profile, recreate)` | pure composition | (wraps `external`/`expensive` `BuildGraph`) | Build the routing graph → `GraphHopperCache`. |
| `osm.ops.GraphHopper.BuildGraph(cache, profile, recreate)` | event | external / expensive | The actual GraphHopper graph bake. |
| `osm.Roads.ZoomBuilder.BuildZoomLayers(cache, graph, min_population, output_dir, max_concurrent)` | event | pure / moderate | Run the LZ selection algorithm and write the layers. |

## Cache / output

Output location is entirely upstream behaviour, driven by the `output_dir` osm-lz
passes to `BuildZoomLayers` (`<output_base>/us`, `<output_base>/europe/germany`, …).
The concrete cache namespaces (the PBF cache, the GraphHopper graph cache) and the
zoom-layer artifact format are owned by fwh_osm — on the fleet these resolve to
`s3://afl-cache/...` via `resolve_output_dir`. See fwh_osm's `cache-and-download`,
`graphhopper`, and `roads` specs for the concrete layout.

## Gotchas & notes

- **Nothing runs without fwh_osm.** These workflows only resolve if a runner has
  fwh_osm's handlers loaded (`fw runner start --domain osm-geocoder --domain
  osm-lz`). osm-lz itself shows `SKIP (no handlers/)` at runner start — that is
  correct; it contributes only FFL.
- **Long, heavy, download-bound.** Each region chain downloads GB-scale PBFs and
  bakes GB-scale graphs; a full `BuildContinentalLZ` is a 12–30 h run. These belong
  on capable (`heavy`) fleet hosts.
- **The region set is hard-coded in FFL.** Adding a country means editing
  `continental_lz_workflows.ffl` (add the `_osm`/`_gh`/`_lz` step trio and extend
  the `yield` aggregation) — there is no `foreach` over a region list. Per the repo
  CLAUDE.md, only compose facets that already exist in a fwh_osm namespace.
- **`output_base` is the only input.** Keep it simple (a plain path String) so
  `tools/submit.sh` serialises it cleanly to JSON.

## Related specs

- [full-pipeline](full-pipeline.md) — `continental.FullContinentalPipeline` runs
  this LZ orchestrator alongside the transit analysis.
- [continental-transit](continental-transit.md) — the sibling GTFS pipeline; same
  compose-only pattern over a different upstream namespace.
- [shared-types](shared-types.md) — `RegionLZResult` / `ContinentalLZSummary`.
- [catalog-and-tooling](catalog-and-tooling.md) — how these workflows are
  registered (no handlers), catalogued, and submitted.
- Upstream (fwh_osm): the `cache`, `graphhopper`, and `roads` feature specs own the
  `OSMCache` / `GraphHopperCache` / `BuildZoomLayers` implementations.
