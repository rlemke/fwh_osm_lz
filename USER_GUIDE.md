# OSM LZ Pipeline — User Guide

> See also: [README](README.md)

## When to Use This Example

Use this as your starting point if you are:
- Composing FFL workflows that span **multiple continental regions** in parallel
- Running **long-duration workflows** (hours) on a Facetwork deployment with MongoDB persistence
- Combining handlers from a separately-installed package
  (osm-geocoder) with your own workflow definitions

## What You'll Learn

1. How an FFL-only example composes handlers from an external package
2. How `uses` declarations resolve cross-package types via the seeded flows
3. How to orchestrate parallel pipelines across continental regions
4. How to structure long-running workflows that span 12-30 hours

## Overview

This example provides FFL workflows that orchestrate two pipelines across
15+ regions:

- **Low-Zoom (LZ) road infrastructure** for US, Canada, and 12 European countries
- **GTFS transit analysis** for 11 agencies (Amtrak, MBTA, CTA, MTA, TransLink,
  TTC, OC Transpo, Deutsche Bahn, SNCF, Renfe, Trenitalia)
- **72+ GB of data** (PBF downloads + GraphHopper routing graphs)
- **12-30 hours** for a full continental run

The handler implementations come from the [osm-geocoder](https://github.com/rlemke/fwh_osm)
package — install that first, then this example's FFL workflows use its
namespaces (`osm.cache.*`, `osm.GraphHopper.*`, `osm.Transit.GTFS.*`).

## Setup

### 1. Install osm-geocoder

```bash
git clone https://github.com/rlemke/fwh_osm.git ~/fw_handlers/fwh_osm
pip install -e ~/fw_handlers/fwh_osm
```

This registers osm-geocoder's handlers via the `facetwork.examples` entry
point. Facetwork's discovery picks them up automatically.

### 2. Seed both flows into MongoDB

```bash
scripts/seed-examples --include "^(osm-lz|osm-geocoder)$"
```

The seeder compiles both flows. Continental-lz's `uses osm.cache.NorthAmerica`
declarations resolve against osm-geocoder's seeded definitions.

### 3. Start the runner

```bash
scripts/start-runner --example osm-geocoder --example osm-lz -- --log-format text
```

Note: pass `--example osm-geocoder` (not osm-lz). osm-geocoder's
handlers are what executes; osm-lz contributes only the
composition workflows.

### 4. Run a workflow

Open http://localhost:8080, click **Workflows**, and pick one of:

| Workflow | Purpose |
|----------|---------|
| `continental.lz.FullContinentalPipeline` | LZ + GTFS bundle |
| `continental.lz.BuildContinentalLZ` | LZ across all 14 regions |
| `continental.gtfs.ContinentalTransitAnalysis` | All 11 transit agencies |

Or via CLI:

```bash
scripts/run-workflow continental.lz.FullContinentalPipeline
```

## Layout

```
fwh_osm_lz/
├── pyproject.toml                            # facetwork.examples entry point
├── README.md
├── CLAUDE.md
├── USER_GUIDE.md
├── agent-spec/
└── src/osm_lz/
    ├── __init__.py                           # ExamplePackage (no-op register_handlers)
    ├── ffl/
    │   ├── continental_types.ffl             # Shared region/agency schemas
    │   ├── continental_lz_workflows.ffl      # Per-region LZ pipelines
    │   ├── continental_gtfs_workflows.ffl    # Per-agency GTFS pipelines
    │   └── continental_full.ffl              # FullContinentalPipeline (LZ + GTFS)
    └── tools/
        ├── _lib/workflows.py                 # workflow catalog metadata
        ├── list-workflows.sh
        └── submit.sh                         # auto-resolves --library FFL paths
```

No Python event-facet handlers — those live in
[fwh_osm](https://github.com/rlemke/fwh_osm). The `tools/` directory
here only ships workflow-submission helpers.

## Why split this from osm-geocoder?

osm-lz is a **higher-level composition** of osm-geocoder facets
into long-running, regionally-parallel workflows. Keeping it separate:

- Lets osm-geocoder ship as a focused, reusable handler package
- Keeps the continental-scale orchestration close to Facetwork's other
  multi-pipeline examples
- Avoids bloating osm-geocoder with workflow scenarios specific to one
  use case (LZ road infrastructure)

If you need to extend this with a new region, edit
`continental_lz_workflows.ffl` and add a new branch — the underlying
handlers are reused unchanged.
