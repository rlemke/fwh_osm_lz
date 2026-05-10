# osm-lz

A standalone [Facetwork](https://github.com/rlemke/facetwork) example package
that orchestrates the **OpenStreetMap Low-Zoom (LZ) road infrastructure
pipeline** and **GTFS transit analysis** across continental regions
(US, Canada, Europe).

This is a **pure FFL workflow catalog** — it contributes 0 Python
handlers. The handlers it composes
(`osm.cache.<continent>.<country>`, `osm.cache.GraphHopper.<continent>.<country>`,
`osm.Roads.ZoomBuilder.*`, `osm.Transit.GTFS.*`) live in the sibling
[fwh_osm](https://github.com/rlemke/fwh_osm) package.

## Install

```bash
# 1. Handlers (required) — provides every event facet the workflows reference
git clone https://github.com/rlemke/fwh_osm.git ~/fw_handlers/fwh_osm
pip install -e ~/fw_handlers/fwh_osm

# 2. This package — provides the workflow catalog
git clone https://github.com/rlemke/fwh_osm_lz.git ~/fw_handlers/fwh_osm_lz
pip install -e ~/fw_handlers/fwh_osm_lz
```

Both packages register under the `facetwork.examples` entry-point group,
making them discoverable by any Facetwork installation in the same
environment.

## Run from a Facetwork checkout

The runner needs the **osm-geocoder** handlers (from fwh_osm) plus the
**osm-lz** workflows (from this repo):

```bash
# Seed the workflow catalog into MongoDB
scripts/seed-examples --include osm-lz

# Start a runner with osm-geocoder's handlers; it picks up osm-lz workflows automatically
scripts/start-runner --example osm-geocoder --example osm-lz -- --log-format text
```

`osm-lz` itself shows `SKIP (no handlers/)` during start-runner — that's
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
├── pyproject.toml                 # facetwork.examples entry point (osm-lz = osm_lz:example)
├── README.md
├── CLAUDE.md
├── USER_GUIDE.md
├── agent-spec/
└── src/osm_lz/
    ├── __init__.py                # exports `example: ExamplePackage` (no-op register_handlers)
    ├── ffl/                       # 4 FFL files: types, lz workflows, gtfs workflows, full pipeline
    └── tools/                     # CLI submission helpers
        ├── _lib/workflows.py      # workflow catalog metadata
        ├── list-workflows.sh
        └── submit.sh
```

## License

Apache 2.0 — see `LICENSE`.
