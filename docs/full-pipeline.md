# Full Continental Pipeline (LZ + Transit bundle)

**Namespace:** `continental` ·
**FFL:** `src/osm_lz/ffl/continental_full.ffl` ·
**Catalog:** `src/osm_lz/catalog.yaml` (`continental.FullContinentalPipeline`, entry point) ·
**Composes:** `continental.lz.BuildContinentalLZ`, `continental.transit.ContinentalTransitAnalysis`

## Overview

`FullContinentalPipeline` is the top-level **"build everything continental"** entry
point: it runs the entire LZ road-infrastructure orchestrator and the entire GTFS
transit analysis in one workflow. It is the preferred single dashboard run when you
want both the continental low-zoom road map and the continental transit summary.

It is the thinnest composition in the package — a two-step `andThen` that calls the
two sub-orchestrators. It composes **osm-lz's own workflows**, which in turn compose
the upstream `fwh_osm` facets; osm-lz still registers no handlers of its own.

## How it works

```
FullContinentalPipeline(output_base)
  ├─ lz      = continental.lz.BuildContinentalLZ(output_base = $.output_base)   → 14 regions
  └─ transit = continental.transit.ContinentalTransitAnalysis()                → 11 agencies
  yield FullContinentalPipeline(lz_regions = 14, transit_agencies = 11)
```

The two steps have no data dependency on each other, so the runtime runs the whole
LZ tree and the whole transit tree in parallel. The workflow's return values
(`lz_regions = 14`, `transit_agencies = 11`) are **literal constants** in the
`yield`, not counts computed from the sub-results — they document the scope of the
run, not a measured tally.

## Fan-out

**Fans out transitively.** This workflow itself is two parallel steps, but each step
is a large fan-out tree: `BuildContinentalLZ` expands to 14 per-country PBF→graph→LZ
chains (see [continental-lz](continental-lz.md) → Fan-out) and
`ContinentalTransitAnalysis` expands to 11 per-agency GTFS chains (see
[continental-transit](continental-transit.md) → Fan-out). At peak, dozens of
distributed tasks are in flight across the fleet. The heavy tail is the LZ side
(GB-scale PBFs and graphs); the transit side is comparatively light.

## Data & fields

`use continental.types` only. The workflow returns two scalars —
`lz_regions: Long`, `transit_agencies: Long` — and declares no schema of its own.
The rich per-region / per-agency results are produced (and cached) inside the two
sub-orchestrators; this bundle surfaces only the top-line region/agency counts.

## External libraries / binaries

**None local** — same as the rest of the package. All compute is upstream
`fwh_osm` handlers, reached transitively through the two sub-orchestrators.

## Facets & workflows

| Workflow | Signature | Returns | Purpose (FFL docstring) |
|---|---|---|---|
| `FullContinentalPipeline` | `(output_base: String = "/data/lz-output")` | `(lz_regions: Long, transit_agencies: Long)` | Runs LZ road infrastructure and GTFS transit analysis in parallel across all regions. |

Composes two osm-lz workflows (not facets): `continental.lz.BuildContinentalLZ` and
`continental.transit.ContinentalTransitAnalysis`. osm-lz declares **no facets of its
own** anywhere. Note: the README/catalog file for this file is
`continental_full.ffl`; the workflow lives in the bare `continental` namespace (its
sub-steps live in `continental.lz` / `continental.transit`).

## Cache / output

No `output_dir` beyond the `output_base` it forwards to `BuildContinentalLZ`
(the transit side takes no output param). All artifacts land wherever the two
sub-pipelines put them — upstream fwh_osm cache namespaces, `s3://afl-cache` on the
fleet.

## Gotchas & notes

- **Longest, heaviest run in the catalog.** It is the union of the LZ (12–30 h) and
  transit trees; run it only on a fleet that can service the LZ load.
- **Return counts are static.** `lz_regions`/`transit_agencies` are hard-coded
  `14`/`11` in the `yield` — they reflect the catalog's design scope, not a runtime
  count of successful sub-results. Don't read them as a success tally.
- **Namespace vs example name.** The workflow is `continental.FullContinentalPipeline`
  (scope = continental); the package/example name is `osm-lz` (which handler family
  it composes). See the repo CLAUDE.md "Naming choices".
- Historical note: an older `USER_GUIDE.md` snippet references
  `continental.lz.FullContinentalPipeline` — the workflow actually lives in the bare
  `continental` namespace per `continental_full.ffl` and the catalog.

## Related specs

- [continental-lz](continental-lz.md) — the LZ half (flagship).
- [continental-transit](continental-transit.md) — the transit half.
- [catalog-and-tooling](catalog-and-tooling.md) — registration + submission.
- [shared-types](shared-types.md) — `continental.types`.
