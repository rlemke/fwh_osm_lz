# osm-lz — Feature Specifications

This directory holds one **spec per osm-lz feature**. Each document follows a common
shape ([`SPEC_TEMPLATE.md`](SPEC_TEMPLATE.md)) and states, for that feature: how it
works, whether and how it **fans out** across the fleet, the **schemas/fields** it
declares, its **workflows**, and — because osm-lz is a **pure-FFL workflow catalog**
— exactly which upstream **`fwh_osm` facets it composes**. Claims are grounded in the
FFL `/** … */` docstrings, the `catalog.yaml` manifest, and the tools.

**The one thing to know about this package:** osm-lz declares **zero event facets and
registers zero handlers of its own** (`src/osm_lz/__init__.py` wires a no-op handler
registrar into a `DomainPackage`). Every workflow here is pure FFL that dispatches to
event facets defined in the sibling [`fwh_osm`](https://github.com/rlemke/fwh_osm)
package — nothing runs unless a runner has fwh_osm's handlers loaded. See
[catalog-and-tooling](catalog-and-tooling.md) for that story.

**Start here:** [**Continental LZ**](continental-lz.md) — the flagship feature
(continental low-zoom road maps: PBF → GraphHopper graph → zoom layers, fanned out
per country).

## Pipelines

| Spec | What it covers |
|------|----------------|
| [continental-lz.md](continental-lz.md) | **Flagship.** Low-zoom (zoom 2–7) road tile layers for the US, Canada, and 12 European countries. Composes `osm.cache.*` → `osm.cache.GraphHopper.*` → `osm.Roads.ZoomBuilder.BuildZoomLayers`; per-country fan-out. |
| [continental-transit.md](continental-transit.md) | GTFS transit analysis for 11 US/Canada/Europe agencies + regional/continental roll-ups. Composes `osm.Transit.GTFS.{DownloadFeed,TransitStatistics,ExtractStops,ExtractRoutes}`; per-agency fan-out. |
| [full-pipeline.md](full-pipeline.md) | `continental.FullContinentalPipeline` — the top-level bundle running the LZ orchestrator and the transit analysis in parallel. |

## Types & packaging

| Spec | What it covers |
|------|----------------|
| [shared-types.md](shared-types.md) | The `continental.types` schemas (`RegionLZResult`, `TransitAgencyResult`, `ContinentalLZSummary`, `ContinentalTransitSummary`) shared by every workflow; the type bridge to upstream result types. |
| [catalog-and-tooling.md](catalog-and-tooling.md) | How the package registers (**no handlers**), the `catalog.yaml` composability manifest (`facets: []` on purpose), `uses`-resolution against fwh_osm, and the `submit` / `list-workflows` CLIs. |

---

*See also the machine-readable capability index at
[`src/osm_lz/catalog.yaml`](../src/osm_lz/catalog.yaml) (workflows by intent —
`facets` is empty here on purpose; the composed facets are indexed in fwh_osm's
`osm_geocoder/catalog.yaml`), the repo [`CLAUDE.md`](../CLAUDE.md),
[`README.md`](../README.md), and [`USER_GUIDE.md`](../USER_GUIDE.md). The
live/queryable interface is the MCP `fw_capabilities` / `fw_catalog_search` /
`fw_describe_handler` tools — for the facets these workflows compose, query the
`osm.*` namespaces served by fwh_osm.*
