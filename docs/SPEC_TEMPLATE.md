<!-- SPEC TEMPLATE — every docs/<feature>.md follows this shape so the set reads
consistently. Delete this comment in real specs. Keep sections in this order;
omit a section only if it genuinely does not apply (say so in one line rather
than dropping the heading silently). Ground every claim in the actual FFL
docstrings / catalog metadata / tools — do not invent behaviour. Because osm-lz
is a PURE-FFL workflow catalog, every spec must name the upstream `fwh_osm`
facets it composes (`osm.cache.*`, `osm.cache.GraphHopper.*`,
`osm.Roads.ZoomBuilder.*`, `osm.Transit.GTFS.*`) and be explicit that this repo
registers no handlers of its own. -->

# <Feature Name>

**Namespace(s):** `continental.<ns>` · **FFL:** `src/osm_lz/ffl/<file>.ffl` ·
**Catalog:** `src/osm_lz/catalog.yaml` / `src/osm_lz/tools/_lib/workflows.py` ·
**Composes (fwh_osm):** `osm.<ns>.<Facet>` (list the upstream event facets)

## Overview
One or two paragraphs: what this feature is for, the request it answers, and where
it sits in the catalog (per-region build → continental roll-up → full bundle, etc.).
State up front that the workflow logic is pure FFL composition — the compute is done
by upstream `fwh_osm` handlers.

## How it works
The composition / data flow, step by step. Name the concrete FFL steps and the shape
of the data at each (`OSMCache` → `GraphHopperCache` → zoom-layer output; GTFS URL →
`GTFSFeed` → stats/stops/routes, etc.). Name every upstream facet each step calls and
whether it is an event facet (needs a handler) or a pure composition facet.

## Fan-out
Does it fan out across the fleet? If yes: what is the fan-out unit (per-country /
per-agency / per-region), what drives it (parallel sibling steps in one `andThen`,
or a nested workflow call), and why it reduces wall-clock. If it is single-region,
say "single region — no fan-out" and why.

## Data & fields
The schemas this feature declares in `continental.types` (field names + types) and
the upstream return types it binds (`ZoomBuilderResult`, `TransitStats`, `OSMCache`,
`GraphHopperCache`, `GTFSFeed`). Note any tuning constants passed to upstream facets
(`min_population`, `profile`, GTFS feed URLs). If the feature declares no schema of
its own, say so.

## External libraries / binaries
osm-lz itself has NO runtime deps beyond `facetwork` + `PyYAML` (see
`pyproject.toml`) and ships no Python operations. The heavy dependencies
(osmium, GraphHopper/Java, `requests`, etc.) belong to the upstream `fwh_osm`
handlers this feature dispatches to — name them here as *composed* dependencies,
not local ones, and point at the fwh_osm spec that owns them.

## Facets & workflows
The workflows this feature defines, with signatures and the one-line purpose from
their FFL `/** … */` docstrings. Then the upstream `fwh_osm` facets each composes,
with their kind (event vs pure composition) and `Effect`/`Cost` mixins as declared
in the fwh_osm FFL. osm-lz declares **no facets of its own** — say so.

## Cache / output
Where the outputs land — this is upstream behaviour. Name the `output_base` /
`output_dir` convention passed in, and note that the actual cache namespaces and
artifact formats (PBF, GraphHopper graph, GeoJSON, tiles) are owned by fwh_osm.
Point at the relevant fwh_osm spec for the concrete cache layout.

## Gotchas & notes
Known pitfalls: the handler-package dependency (nothing runs without fwh_osm
installed + a runner loading its handlers), long durations, feed-URL rot,
namespace-vs-example-name decoupling, `--library` closure requirements.

## Related specs
Links to the osm-lz specs this feature composes with, and pointers to the owning
fwh_osm feature specs.
