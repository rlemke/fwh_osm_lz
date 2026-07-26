# Continental GTFS Transit Analysis

**Namespace:** `continental.transit` ·
**FFL:** `src/osm_lz/ffl/continental_gtfs_workflows.ffl` ·
**Types:** `src/osm_lz/ffl/continental_types.ffl` (`TransitAgencyResult`, `ContinentalTransitSummary`) ·
**Catalog:** `src/osm_lz/catalog.yaml` (15 entries) / `src/osm_lz/tools/_lib/workflows.py` ·
**Composes (fwh_osm):** `osm.Transit.GTFS.DownloadFeed`, `osm.Transit.GTFS.TransitStatistics`,
`osm.Transit.GTFS.ExtractStops`, `osm.Transit.GTFS.ExtractRoutes`

## Overview

This feature downloads and analyses **GTFS static feeds** for 11 major transit
agencies across the US, Canada, and Europe, and rolls them up into per-region and
continental summaries. It answers "analyze public-transit coverage across North
America and Europe" as one run.

Like the whole package it is **pure FFL composition** — osm-lz registers no
handlers; every step dispatches to an upstream `osm.Transit.GTFS.*` event facet
defined in `fwh_osm` (`handlers/routes/ffl/osmgtfs.ffl`).

## How it works

Each of the 11 per-agency workflows is the identical four-step chain in one
`andThen`, differing only in the feed URL:

1. `feed = osm.Transit.GTFS.DownloadFeed(url = "<agency feed URL>")` → a `GTFSFeed`
   (download + unzip the GTFS static bundle). Event facet, Effect `external` / Cost
   `expensive`.
2. `stats = osm.Transit.GTFS.TransitStatistics(feed = feed.feed)` → `TransitStats`
   (aggregate counts by route type). Event facet, Effect `pure` / Cost `cheap`.
3. `stops = osm.Transit.GTFS.ExtractStops(feed = feed.feed)` → stop-point GeoJSON.
   Event facet, Effect `pure` / Cost `cheap`.
4. `routes = osm.Transit.GTFS.ExtractRoutes(feed = feed.feed)` → route-line GeoJSON.
   Event facet, Effect `pure` / Cost `cheap`.

The workflow then `yield`s `result = stats.stats` into a
`continental.types.TransitAgencyResult`. (Stops and routes run for their extracted
GeoJSON side-effects; the returned summary is the statistics.)

Three regional aggregators (`AnalyzeUSTransit`, `AnalyzeCanadaTransit`,
`AnalyzeEuropeTransit`) call their member per-agency workflows as steps and
concatenate the results into a `[TransitAgencyResult]`. `ContinentalTransitAnalysis`
calls all three aggregators and rolls up into a `ContinentalTransitSummary`.

## Fan-out

**Fans out per-agency via parallel sibling steps (no `foreach`).** Inside each
regional aggregator the member agencies are independent steps, so e.g.
`AnalyzeUSTransit`'s Amtrak/MBTA/CTA/MTA chains run concurrently, and
`ContinentalTransitAnalysis`'s three regional aggregators run concurrently — so all
11 agencies can be in flight at once, each on whichever runner claims it. The
fan-out unit is **per-agency**. Each agency chain is light (one download + three
`cheap` analyses), so this pipeline is far cheaper than the LZ one.

## Data & fields

Schemas declared in `continental.types` (see [shared-types](shared-types.md)):

- `TransitAgencyResult { agency_name, stop_count: Long, route_count: Long,
  trip_count: Long, has_shapes: Boolean }` — the per-agency return.
- `ContinentalTransitSummary { us_agencies, canada_agencies, europe_agencies,
  total_agencies: Long }` — the continental roll-up.

Upstream types bound: `GTFSFeed`, `osm.Transit.GTFS.TransitStats` (and the
`StopResult` / `GTFSRouteFeatures` returned by the extract steps), all defined in
fwh_osm.

The one piece of real per-agency data osm-lz hard-codes is the **feed URL** passed
to `DownloadFeed`. The 11 agencies and their sources:

- **US (4):** Amtrak (`content.amtrak.com`), MBTA/Boston (`cdn.mbta.com`),
  CTA/Chicago (`transitchicago.com`), MTA/NYC Subway (`web.mta.info`).
- **Canada (3):** TransLink/Vancouver (`gtfs.translink.ca`), TTC/Toronto
  (`opendata.toronto.ca`), OC Transpo/Ottawa (`octranspo.com`).
- **Europe (4):** Deutsche Bahn (`download.gtfs.de`), SNCF TER/France
  (`opendatasoft.com`), Renfe/Spain (`renfe.com`), Trenitalia/Italy
  (`trenitalia.com`).

## External libraries / binaries

**None local.** No Python operations ship here. The GTFS download/parse/extract
work (HTTP fetch, zip handling, GeoJSON assembly) is done by the upstream
`osm.Transit.GTFS.*` handlers in fwh_osm — see fwh_osm's `routes` feature spec for
those dependencies.

## Facets & workflows

Workflows defined here (all take **no parameters**):

| Group | Workflows | Returns |
|---|---|---|
| Per-agency (11) | `AnalyzeAmtrak`, `AnalyzeMBTA`, `AnalyzeCTA`, `AnalyzeMTA`, `AnalyzeTransLink`, `AnalyzeTTC`, `AnalyzeOCTranspo`, `AnalyzeDeutscheBahn`, `AnalyzeSNCF`, `AnalyzeRenfe`, `AnalyzeTrenitalia` | `TransitAgencyResult` |
| Regional roll-ups (3) | `AnalyzeUSTransit`, `AnalyzeCanadaTransit`, `AnalyzeEuropeTransit` | `[TransitAgencyResult]` |
| Continental roll-up (1) | `ContinentalTransitAnalysis` | `ContinentalTransitSummary` |

Upstream `fwh_osm` facets composed (osm-lz declares **no facets of its own**):

| Facet | Kind | Effect/Cost | Role |
|---|---|---|---|
| `osm.Transit.GTFS.DownloadFeed(url)` | event | external / expensive | Fetch + unzip the GTFS static feed → `GTFSFeed`. |
| `osm.Transit.GTFS.TransitStatistics(feed)` | event | pure / cheap | Aggregate transit statistics by route type. |
| `osm.Transit.GTFS.ExtractStops(feed)` | event | pure / cheap | Extract stop points to GeoJSON. |
| `osm.Transit.GTFS.ExtractRoutes(feed)` | event | pure / cheap | Extract route geometries to GeoJSON. |

## Cache / output

Output is upstream behaviour: `DownloadFeed` caches the feed and the extract facets
write GeoJSON under fwh_osm's GTFS cache namespace (on the fleet, `s3://afl-cache`).
osm-lz passes no `output_dir` here — the per-agency workflows return only the
statistics summary. See fwh_osm's `routes` spec for the concrete cache/output.

## Gotchas & notes

- **Feed URLs rot.** The 11 agency URLs are baked into the FFL; agencies move or
  reorganise their GTFS endpoints, so a `DownloadFeed` failure is often a stale URL,
  not a pipeline bug. Fix the URL in `continental_gtfs_workflows.ffl`.
- **Nothing runs without fwh_osm** — the `osm.Transit.GTFS.*` handlers must be
  loaded on the runner; osm-lz contributes only these composition workflows.
- **Stops/routes are extracted but not returned.** The per-agency workflows yield
  only `stats.stats`; the stop/route GeoJSON is produced as a cached side-effect. If
  you need those paths surfaced, extend the workflow's return (and the matching
  `TransitAgencyResult`).
- **Zero-input workflows.** These take no parameters, so submit them with an empty
  `--inputs '{}'` (the default).

## Related specs

- [full-pipeline](full-pipeline.md) — `FullContinentalPipeline` runs
  `ContinentalTransitAnalysis` alongside the LZ orchestrator.
- [continental-lz](continental-lz.md) — the sibling road pipeline.
- [shared-types](shared-types.md) — `TransitAgencyResult` / `ContinentalTransitSummary`.
- [catalog-and-tooling](catalog-and-tooling.md) — registration, catalog, submission.
- Upstream (fwh_osm): the `routes` feature spec owns the `osm.Transit.GTFS.*`
  handlers.
