# Shared Types & Schemas (`continental.types`)

**Namespace:** `continental.types` ·
**FFL:** `src/osm_lz/ffl/continental_types.ffl` ·
**Composes (fwh_osm):** none directly — but bridges to upstream result types
(`ZoomBuilderResult`, `TransitStats`, `GTFSFeed`) via the workflow `yield`s.

## Overview

`continental.types` is the small shared-schema namespace that every osm-lz
workflow file `use`s. It defines the four result/summary schemas the LZ and transit
workflows return, so the per-region and per-agency workflows have a stable return
shape and the roll-ups have something typed to aggregate into. It declares **no
facets and no handlers** — it is pure type declarations.

## How it works

The file is four `schema` declarations inside `namespace continental.types`. Each of
the other three FFL files opens with `use continental.types` (and `use osm.types`)
so these schema names resolve when a workflow declares its return clause. The
`tools/_lib/workflows.py` catalog passes all four FFL files together as the
`--library` closure precisely because `continental.types` is referenced by all of
them (see [catalog-and-tooling](catalog-and-tooling.md)).

## Fan-out

Not applicable — this is a type-only namespace with no executable steps. (Heading
kept per the template; it genuinely does not apply.)

## Data & fields

The four schemas, verbatim from the FFL:

| Schema | Fields | Used by |
|---|---|---|
| `RegionLZResult` | `region: String`, `total_edges: Long`, `selected_edges: Long`, `output_dir: String` | Per-region LZ workflow returns ([continental-lz](continental-lz.md)). |
| `TransitAgencyResult` | `agency_name: String`, `stop_count: Long`, `route_count: Long`, `trip_count: Long`, `has_shapes: Boolean` | Per-agency transit workflow returns ([continental-transit](continental-transit.md)). |
| `ContinentalLZSummary` | `us_total_edges`, `us_selected_edges`, `canada_total_edges`, `canada_selected_edges`, `europe_total_edges`, `europe_selected_edges`, `total_regions` — all `Long` | `BuildContinentalLZ` roll-up return. |
| `ContinentalTransitSummary` | `us_agencies`, `canada_agencies`, `europe_agencies`, `total_agencies` — all `Long` | `ContinentalTransitAnalysis` roll-up return. |

Note the type bridge: the LZ workflows declare a `RegionLZResult` return but `yield`
the upstream `ZoomBuilderResult` from `BuildZoomLayers`; the transit workflows
declare `TransitAgencyResult` but `yield` the upstream `TransitStats` from
`TransitStatistics`. The whole set compiles clean against the fwh_osm library (see
`tests/test_ffl_compiles.py`), which supplies those upstream types.

## External libraries / binaries

None — type declarations only.

## Facets & workflows

None. This namespace declares only schemas. osm-lz declares **no event facets**
anywhere in the package.

## Cache / output

Not applicable — no executable steps, no cache, no output.

## Gotchas & notes

- **Referenced by every workflow file**, so it must always be in the `--library`
  closure when compiling or submitting any osm-lz workflow — which is exactly why
  `tools/submit.sh` passes all four FFL files together.
- **Schema-only namespace.** Per the FFL grammar, schemas (and workflows) must live
  inside a namespace; this file is the canonical place to add a new shared result
  type if you extend the catalog.

## Related specs

- [continental-lz](continental-lz.md) — consumes `RegionLZResult` / `ContinentalLZSummary`.
- [continental-transit](continental-transit.md) — consumes `TransitAgencyResult` / `ContinentalTransitSummary`.
- [catalog-and-tooling](catalog-and-tooling.md) — why all four FFL files ship as one closure.
