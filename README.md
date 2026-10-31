# osm-lz

A standalone [Facetwork](https://github.com/rlemke/facetwork) example package
that orchestrates the **OpenStreetMap Low-Zoom (LZ) road infrastructure
pipeline** and **GTFS transit analysis** across continental regions
(US, Canada, Europe).

This is a **pure FFL workflow catalog** — it contributes 0 Python
handlers (the Python here plans, merges and publishes the world low-zoom run;
it registers nothing). The handlers it composes
(`osm.cache.<continent>.<country>`, `osm.cache.GraphHopper.<continent>.<country>`,
`osm.Roads.ZoomBuilder.*`, `osm.Transit.GTFS.*`) live in the sibling
[fwh_osm](https://github.com/rlemke/fwh_osm) package.

## FFL at a glance

This domain is **pure [FFL](https://github.com/rlemke/facetwork/blob/main/docs/reference/language/grammar.md)**
— no handlers of its own, only workflows composed over `fwh_osm`'s facets. A step
is `name = Facet(args)`, and each step that references the previous one is ordered
behind it:

```ffl
namespace my.lz {

    use osm.cache.NorthAmerica
    use osm.cache.GraphHopper.NorthAmerica
    use osm.Roads.ZoomBuilder

    /** US low-zoom road layers from the cached PBF. */
    workflow MyUSLowZoom(output_base: String = "/data/lz-output", min_population: Long = 50000) => (edges: Long, out_dir: String) andThen {

        us_osm = osm.cache.NorthAmerica.UnitedStates()

        us_gh = osm.cache.GraphHopper.NorthAmerica.UnitedStates(cache = us_osm.cache)

        lz = osm.Roads.ZoomBuilder.BuildZoomLayers(
            cache = us_osm.cache,
            graph = us_gh.graph,
            min_population = $.min_population,
            output_dir = $.output_base ++ "/us")

        yield MyUSLowZoom(edges = lz.result.selected_edges, out_dir = lz.result.output_dir)
    }
}
```

```bash
fw ffl run --primary my.ffl --library <this domain's + fwh_osm's ffl> \
  --workflow my.lz.MyUSLowZoom --task-list osm
```

📖 **[docs/ffl-examples.md](docs/ffl-examples.md)** — the full example gallery:
two regions in parallel, wrapping the shipped pipelines, transit rollups, `when`
checks on GTFS shapes, and call-time `Timeout`/`Retry`/`catch` for the long
continental builds. Every snippet there is compile-checked against both domains.

## Feature specifications

Every osm-lz feature has a spec in [**`docs/`**](docs/README.md) — how it works,
whether/how it **fans out**, the **schemas/fields** it declares, its **workflows**,
and (because this is a pure-FFL catalog) exactly which upstream **`fwh_osm` facets it
composes**. This package declares **no facets and registers no handlers of its own**;
start with the flagship [**Continental LZ**](docs/continental-lz.md) and the full
index in [`docs/README.md`](docs/README.md).

| Area | Specs |
|------|-------|
| **Pipelines** | [continental-lz](docs/continental-lz.md) (flagship) · [continental-transit](docs/continental-transit.md) · [full-pipeline](docs/full-pipeline.md) |
| **Types & packaging** | [shared-types](docs/shared-types.md) · [catalog-and-tooling](docs/catalog-and-tooling.md) |

## Install

```bash
# 1. Handlers (required) — provides every event facet the workflows reference
git clone https://github.com/rlemke/fwh_osm.git ~/fw_handlers/fwh_osm
pip install -e ~/fw_handlers/fwh_osm

# 2. This package — provides the workflow catalog
git clone https://github.com/rlemke/fwh_osm_lz.git ~/fw_handlers/fwh_osm_lz
pip install -e ~/fw_handlers/fwh_osm_lz
```

Both packages register under the `facetwork.domains` entry-point group,
making them discoverable by any Facetwork installation in the same
environment.

## Run from a Facetwork checkout

The runner needs the **osm-geocoder** handlers (from fwh_osm) plus the
**osm-lz** workflows (from this repo):

```bash
# Seed the workflow catalog into MongoDB
fw ffl seed --include osm-lz

# Start a runner with osm-geocoder's handlers; it picks up osm-lz workflows automatically
fw runner start --domain osm-geocoder --domain osm-lz -- --log-format text
```

`osm-lz` itself shows `SKIP (no handlers/)` during `fw runner start` — that's
correct, the example contributes only FFL.

## Run a workflow

From the dashboard at http://localhost:8080, or from the shell using the
helpers under `src/osm_lz/tools/`:

```bash
# List every workflow this catalog ships
src/osm_lz/tools/list-workflows.sh

# Submit a workflow by qualified name
src/osm_lz/tools/submit.sh --workflow continental.lz.BuildEuropeLowZoom \
                          --inputs '{"output_base": "/data/lz-output"}'

src/osm_lz/tools/submit.sh --workflow continental.transit.AnalyzeAmtrak

# Submit the all-regions roll-up
src/osm_lz/tools/submit.sh --workflow continental.lz.BuildContinentalLZ \
                          --inputs '{"output_base": "/data/lz-output"}'
```

The submit CLI auto-resolves all 4 library FFL files this catalog
ships, so you don't have to list `--library` flags by hand.

## Workflows

| Workflow | Purpose |
|----------|---------|
| `continental.FullContinentalPipeline` | Bundle: LZ road infra + GTFS transit |
| `continental.lz.BuildContinentalLZ` | LZ pipeline across all 14 regions |
| `continental.lz.BuildUSLowZoom` | LZ pipeline for the United States |
| `continental.lz.BuildCanadaLowZoom` | LZ pipeline for Canada |
| `continental.lz.BuildEuropeLowZoom` | LZ pipeline for the 12 European regions |
| `continental.transit.ContinentalTransitAnalysis` | Bundle: every GTFS agency below |
| `continental.transit.Analyze{Amtrak,MBTA,CTA,MTA,…}` | Per-agency GTFS feeds (11 agencies) |
| `continental.lz.states.BuildStateGraphs` | Build (or reuse) the routing graphs for a list of region extracts, serially by default |
| `continental.lz.states.BuildStateLowZoomMap` | ONE region's graded low-zoom map (z2–z7 tilesets + viewer); the router must already serve that region |
| `continental.lz.states.BuildCountryLowZoomMap` | A country's top bands (z2–z4) routed over one major-roads graph, city pairs routed as chunk tasks |
| `continental.lz.states.RetileStateMap` | Rebuild a region's tiles + viewer from its existing layer files, no re-routing |

The `continental.lz.states` workflows run **one region per run**: the low-zoom
builder scores roads by routing city pairs, and a GraphHopper server holds one
region. They are what the world run below drives. `submit.sh` /
`list-workflows.sh` know only the continental workflows (their catalog lists the
four `continental_*.ffl` files); submit the `states` workflows from the
dashboard or with `fw ffl run`.

## World low-zoom run (`tools/lz_world.py`)

`tools/lz_world.py` drives a low-zoom map per country for the whole world,
continent by continent: a country at or under `--max-mb` (400) is one map, a
bigger one is split into its sub-regions (cut first with
`osm.planet.BuildAdminSetWorkflow` where the store has none). Per continent it
cuts, builds every leaf's graph, then builds up to `FW_LZ_MAPS_IN_FLIGHT` (4) maps
at once, each behind its own region router (`fw fleet router add`), and publishes
each viewer into the gallery's world tree (`cache/osm/maps/lz/<key>/`). It only
sequences submitted workflows — a router is fleet configuration, which no FFL step
can express. Progress is in a ledger under `FW_LZ_STATE_DIR`
(default `~/.facetwork/lz-world`), so the driver can be killed and restarted.

```bash
python tools/lz_world.py --dry                       # the plan, with estimates
python tools/lz_world.py --continents central-america
```

`tools/lz_merge.py` merges finished region maps into one map (e.g. every US state)
without re-routing: importance stays per region and routes stop at region borders,
and the merged page says so. The planning, merge, publish and re-tile logic live in
`src/osm_lz/world_{plan,merge,publish,retile}.py`, with tests under `tests/`.

The internal FFL namespaces (`continental.lz`, `continental.transit`,
`continental.types`, `continental`) describe the multi-region scope of
the workflows; the example name `osm-lz` describes which Facetwork
handler family they compose against.

## Regions covered

| Region | PBF Size | GH Graph | LZ Time (est.) |
|--------|----------|----------|-----------------|
| United States | ~9 GB | ~15 GB | 4-8 hrs |
| Canada | ~3 GB | ~5 GB | 1-3 hrs |
| Germany | ~3.8 GB | ~6 GB | 2-4 hrs |
| France | ~3.8 GB | ~6 GB | 2-4 hrs |
| UK | ~1.3 GB | ~2 GB | 1-2 hrs |
| Spain | ~1.0 GB | ~1.5 GB | 1-2 hrs |
| Italy | ~1.5 GB | ~2.5 GB | 1-2 hrs |
| Poland | ~1.2 GB | ~2 GB | 1-2 hrs |
| Netherlands | ~1.1 GB | ~1.5 GB | 30-60 min |
| Belgium | ~0.4 GB | ~0.5 GB | 15-30 min |
| Switzerland | ~0.4 GB | ~0.5 GB | 15-30 min |
| Austria | ~0.5 GB | ~0.7 GB | 20-40 min |
| Sweden | ~0.8 GB | ~1.2 GB | 30-60 min |
| Norway | ~0.6 GB | ~0.9 GB | 20-40 min |
| **Total** | **~28 GB** | **~44 GB** | **12-30 hrs** |

## GTFS agencies covered (11)

**US**: Amtrak, MBTA (Boston), CTA (Chicago), MTA (NYC Subway)
**Canada**: TransLink (Vancouver), TTC (Toronto), OC Transpo (Ottawa)
**Europe**: Deutsche Bahn, SNCF (France), Renfe (Spain), Trenitalia

## Layout

```
fwh_osm_lz/
├── pyproject.toml                 # facetwork.domains entry point (osm-lz = osm_lz:example)
├── README.md
├── CLAUDE.md
├── USER_GUIDE.md
├── agent-spec/
├── tools/                         # lz_world.py (world run driver), lz_merge.py
└── src/osm_lz/
    ├── __init__.py                # exports `domain: DomainPackage` (no-op register_handlers)
    ├── catalog.yaml / catalog.py  # composability manifest (workflows by intent)
    ├── world_{plan,merge,publish,retile}.py  # world low-zoom run logic
    ├── ffl/                       # 6 FFL files: types, lz/gtfs workflows, full pipeline, per-region lz + retile
    └── tools/                     # CLI submission helpers
        ├── _lib/workflows.py      # workflow catalog metadata
        ├── list-workflows.sh
        └── submit.sh
```

## License

Apache 2.0 — see `LICENSE`.
