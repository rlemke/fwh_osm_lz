# FFL Examples — `osm-lz`

This domain is **pure FFL**: no handlers of its own, only workflows composed over
`fwh_osm`'s facets. That makes it the best worked example of the thing FFL is for
— assembling someone else's primitives into a new pipeline without writing Python.

Every numbered scenario is a **complete, compilable FFL file**. Copy one into
`my.ffl` and run it with **both** domains as libraries:

```bash
fw ffl run --primary my.ffl \
  --library ~/fw_handlers/fwh_osm_lz/src/osm_lz/ffl/continental_types.ffl \
  --library ~/fw_handlers/fwh_osm/src/osm_geocoder/ffl/geocoder.ffl \
  --workflow my.lz.<WorkflowName> --task-list osm
```

(Add a `--library` for each FFL file your workflow touches, or
`fw ffl seed --include osm-lz osm-geocoder` once.) A runner serving the `osm`
namespace must be up. Every block below is compile-checked against this domain's
FFL **plus** `fwh_osm`'s.

New to the language? Start with the
[FFL grammar](https://github.com/rlemke/facetwork/blob/main/docs/reference/language/grammar.md)
and the [canonical examples](https://github.com/rlemke/facetwork/tree/main/examples/canonical).

---

## The building blocks

| Declaration | Role |
|---|---|
| `continental.types.*` | Schemas: `RegionLZResult`, `TransitAgencyResult`, `ContinentalLZSummary`, `ContinentalTransitSummary` |
| `continental.lz.BuildUSLowZoom` / `BuildCanadaLowZoom` / `BuildEuropeLowZoom` / `BuildContinentalLZ` | The low-zoom road pipelines |
| `continental.transit.Analyze*` (Amtrak, MBTA, CTA, MTA, TransLink, TTC, OC Transpo, DB, SNCF, Renfe, Trenitalia) | Per-agency GTFS analysis |
| `continental.transit.AnalyzeUSTransit` / `AnalyzeCanadaTransit` / `AnalyzeEuropeTransit` / `ContinentalTransitAnalysis` | Regional + continental rollups |
| `continental.FullContinentalPipeline` | Everything, end to end |

Borrowed from `fwh_osm`: `osm.cache.<Continent>.<Region>()`,
`osm.cache.GraphHopper.<Continent>.<Region>(cache)`,
`osm.Roads.ZoomBuilder.BuildZoomLayers(cache, graph, min_population, output_dir)`,
and the GTFS facets.

---

## 1. Run what ships — no FFL to write

```bash
fw ffl seed --include osm-lz

fw ffl run --workflow continental.lz.BuildUSLowZoom \
  --inputs '{"output_base": "/data/lz-output"}' --task-list osm

fw ffl run --workflow continental.transit.ContinentalTransitAnalysis --task-list osm
```

Write FFL when you want a different region set, your own population threshold, or
a different rollup.

## 2. The pattern this domain is made of

Cache → routing graph → zoom layers. Three steps, each referencing the previous
one, which is what orders them. This is `BuildUSLowZoom` written out.

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

Rules visible above: `=>` sits on the **same line** as the closing `)`; references
are always `step.field` (schema results nest: `lz.result.selected_edges`); `++`
concatenates; `$.output_base` reads the workflow's parameter.

## 3. Two regions in parallel

Nothing links the US branch to the Canada branch, so the runtime dispatches both
at once — two graphs built on two runners.

```ffl
namespace my.lz {

    use osm.cache.NorthAmerica
    use osm.cache.GraphHopper.NorthAmerica
    use osm.Roads.ZoomBuilder

    /** US + Canada, concurrently. */
    workflow NorthAmericaLowZoom(output_base: String = "/data/lz-output") => (us_edges: Long, ca_edges: Long) andThen {

        us_osm = osm.cache.NorthAmerica.UnitedStates()
        us_gh = osm.cache.GraphHopper.NorthAmerica.UnitedStates(cache = us_osm.cache)
        us_lz = osm.Roads.ZoomBuilder.BuildZoomLayers(
            cache = us_osm.cache, graph = us_gh.graph,
            min_population = 50000, output_dir = $.output_base ++ "/us")

        ca_osm = osm.cache.NorthAmerica.Canada()
        ca_gh = osm.cache.GraphHopper.NorthAmerica.Canada(cache = ca_osm.cache)
        ca_lz = osm.Roads.ZoomBuilder.BuildZoomLayers(
            cache = ca_osm.cache, graph = ca_gh.graph,
            min_population = 50000, output_dir = $.output_base ++ "/canada")

        yield NorthAmericaLowZoom(
            us_edges = us_lz.result.selected_edges,
            ca_edges = ca_lz.result.selected_edges)
    }
}
```

## 4. Compose workflows, don't duplicate them

The shipped workflows are steps like any other. Wrapping beats forking.

```ffl
namespace my.lz {

    use continental.lz

    /** Wrap the shipped US pipeline and reshape its result. */
    workflow USLowZoomHeadline(output_base: String = "/data/lz-output") => (headline: String, edges: Long) andThen {

        built = continental.lz.BuildUSLowZoom(output_base = $.output_base)

        yield USLowZoomHeadline(
            headline = "low-zoom layers in " ++ built.result.output_dir,
            edges = built.result.selected_edges)
    }
}
```

## 5. Roll several agencies up

`continental.transit.Analyze*` all return the same `TransitAgencyResult`, so a
rollup is just a workflow that calls several of them and yields the fields it
cares about. They're independent, so they run **in parallel**.

```ffl
namespace my.lz {

    use continental.transit

    /** Three agencies at once. */
    workflow MyTransitRollup() => (agencies: Long, amtrak_stops: Long) andThen {

        amtrak = continental.transit.AnalyzeAmtrak()
        mbta = continental.transit.AnalyzeMBTA()
        cta = continental.transit.AnalyzeCTA()

        yield MyTransitRollup(
            agencies = 3,
            amtrak_stops = amtrak.result.stop_count)
    }
}
```

## 6. Branch on a result — `when`

A `when` block hangs off the step it inspects: inside a case `$` is that step and
`$$` reaches the workflow. Every `when` needs a default case, last, and conditions
must be real `Boolean`s (no truthy coercion).

```ffl
namespace my.lz {

    use continental.transit

    /** Flag an agency whose GTFS feed has no shapes. */
    workflow ShapeCheck() => (status: String, routes: Long) andThen {

        amtrak = continental.transit.AnalyzeAmtrak() andThen when {
            case $.result.has_shapes == true => {
                yield ShapeCheck(status = "shapes_present", routes = $.result.route_count)
            }
            case _ => {
                yield ShapeCheck(status = "no_shapes", routes = $.result.route_count)
            }
        }
    }
}
```

## 7. Call-time mixins and `catch`

Continental graph builds are the longest jobs in the fleet; a call site can raise
the clock and degrade cleanly.

```ffl
namespace my.lz {

    use continental.lz

    /** Long clock, one retry, clean failure. */
    workflow PatientUSLowZoom(output_base: String = "/data/lz-output") => (status: String, edges: Long) andThen {

        built = continental.lz.BuildUSLowZoom(
            output_base = $.output_base) with Timeout(minutes = 480) with Retry(maxAttempts = 2, backoffSeconds = 300) catch {
            yield PatientUSLowZoom(status = "lz_build_failed", edges = 0)
        }

        yield PatientUSLowZoom(status = "completed", edges = built.result.selected_edges)
    }
}
```

---

## Cheat sheet

| You want to… | Write |
|---|---|
| Read a workflow/step parameter | `$.name` (`$$.name` one level out) |
| Read a previous step's result | `stepname.field` — schema results nest: `lz.result.selected_edges` |
| Run steps in parallel | write them with no reference between them |
| Reuse another domain's facets | `use <their.namespace>` and pass both FFL sets as `--library` |
| Reuse a workflow | call it as a step |
| More time / retries for one call | `… with Timeout(minutes = 480) with Retry(maxAttempts = 2, backoffSeconds = 300)` |
| Handle a step failure | `step = Facet(…) catch { yield … }` |
| Branch | `step = Facet(…) andThen when { case <bool> => { … } case _ => { … } }` |
| Concatenate strings | `a ++ b` |

**Validate before you run:** `afl my.ffl --check` or MCP `fw_validate`. Every error
carries a `rule_id` — fetch `fw://docs/rules/{rule_id}` for a wrong/right pair.

## See also

- [`docs/README.md`](README.md) — per-feature specs for this domain
- [`fwh_osm`](https://github.com/rlemke/fwh_osm) — where every facet used here is declared
- [FFL grammar](https://github.com/rlemke/facetwork/blob/main/docs/reference/language/grammar.md) ·
  [canonical examples](https://github.com/rlemke/facetwork/tree/main/examples/canonical) ·
  [relative `$`-scoping](https://github.com/rlemke/facetwork/blob/main/docs/architecture/ffl-relative-scoping.md)
- The domain's FFL under `src/osm_lz/ffl/` — the source of truth for every signature above
