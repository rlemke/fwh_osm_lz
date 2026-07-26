# Package Registration, Catalog & Tooling

**Package:** `osm_lz` (`src/osm_lz/`) ·
**Entry point:** `pyproject.toml` → `[project.entry-points."facetwork.domains"]` `osm-lz = "osm_lz:domain"` ·
**Registrar:** `src/osm_lz/__init__.py` (`DomainPackage`, no-op `_register_no_handlers`) ·
**Manifest:** `src/osm_lz/catalog.yaml` + `src/osm_lz/catalog.py` ·
**Tools:** `src/osm_lz/tools/` (`submit`, `list-workflows`, `_lib/workflows.py`) + root `./osm-lz` dispatcher

## Overview

This spec is the honest "what does this package actually contribute?" write-up. The
answer: **FFL workflows and metadata, and nothing else.** osm-lz is the canonical
demo of pure-FFL composition — it declares zero event facets and **registers zero
handlers**, and everything it runs is served by the sibling `fwh_osm` package. This
spec documents the seams that make that work: the domain-package registration, the
composability manifest, and the submission tooling.

## How it works

**Registration (registers nothing).** `src/osm_lz/__init__.py` builds a
`DomainPackage(name="osm-lz", ffl_dir=…/ffl, register_handlers=_register_no_handlers)`
and exports it as `domain`. `_register_no_handlers` is a deliberate no-op — its
docstring states osm-lz "contributes only FFL workflows — the handlers it composes
are registered by the fwh_osm package." Discovery still calls it so the package
surfaces (`--domain osm-lz` / seed), but it adds **no rows** to
`handler_registrations`. At runner start osm-lz reports `SKIP (no handlers/)`, which
is correct.

**`uses`/`use` resolution.** The workflow files `use osm.types` /
`use continental.types` and `uses osm.cache.NorthAmerica`, `uses
osm.cache.GraphHopper.Europe`, `uses osm.Roads.ZoomBuilder`, `uses osm.Transit.GTFS`.
Those `osm.*` namespaces are **not** defined in this repo — they resolve against the
deployed/seeded fwh_osm library. `tests/test_ffl_compiles.py` mirrors this by
compiling each osm-lz FFL file with the entire fwh_osm FFL library supplied as the
`--library` closure; a clean compile means the file is genuinely deployable.

**The composability manifest (`catalog.yaml` + `catalog.py`).** A machine-readable
index of the package's reusable **workflows** (each with an intent `summary`, `tags`,
and `param_schema`) so an LLM can discover/reuse by intent — the package-local
analogue of the platform's `fw_catalog_match`. `catalog.py` exposes
`load_manifest()` / `workflows()` / `facets()`. Crucially, `facets: []` is **empty
on purpose**: osm-lz declares no facets; the facet capability index for everything it
composes lives in fwh_osm's `osm_geocoder/catalog.yaml`. `tests/test_catalog_manifest.py`
asserts the shape (every workflow has a non-empty summary/tags/param_schema and its
leaf name really appears as a `workflow <Leaf>` in the FFL).

**Submission tooling (`tools/`).** Because there are no facets to wrap, the CLIs wrap
**workflow submission** instead:

- `tools/_lib/workflows.py` — the static `CATALOG` of `WorkflowInfo(qualified_name,
  description, primary, inputs)`, plus `find()` and `library_files()`. It knows the
  four FFL files are namespace-coupled, so `library_files(primary)` returns "all four
  minus the primary" — guaranteeing `continental.types` (and cross-file references)
  are always in scope.
- `tools/list-workflows.sh` (`list_workflows.py`) — prints the catalog; `--qualified-only`
  gives a pipe-friendly name list.
- `tools/submit.sh` (`submit.py`) — a thin wrapper around `facetwork-submit` whose
  only value-add is resolving `--primary` + `--library` from the catalog and
  validating the `--inputs` JSON before it reaches Mongo.
- root `./osm-lz` — the generic `fw`-style domain dispatcher shared across all
  `fwh_*` repos; it auto-lists `tools/*.sh` and the FFL workflows (each with its
  `fw ffl run` line).

## Fan-out

Not applicable — this is packaging/tooling, not a workflow. (Fan-out for the actual
pipelines is covered in [continental-lz](continental-lz.md) and
[continental-transit](continental-transit.md).)

## Data & fields

The `WorkflowInfo` dataclass (`qualified_name`, `description`, `primary: Path`,
`inputs: dict[str,str]`) and the `catalog.yaml` workflow entries (`qualified_name`,
`summary`, `tags`, `entry_point: true`, `param_schema`). The manifest lists **20
workflows** (matching the FFL); `facets` is empty. `FFL_FILES` = the four
`continental_*.ffl` files, resolved package-relative so editable and zip installs
behave identically.

## External libraries / binaries

The whole package's declared runtime deps are just `facetwork>=0.31.0` and
`PyYAML>=6.0` (`pyproject.toml`); `PyYAML` is used only to read `catalog.yaml`. Dev
extras: `pytest`, `pytest-cov`, `ruff`. The CLAUDE.md code-review checklist forbids
adding `pymongo`/`requests` here — submission shells out to the `facetwork-submit`
binary rather than talking to Mongo directly. There are **no** handler/binary deps
(osmium, GraphHopper, etc.) — those belong to fwh_osm.

## Facets & workflows

No facets. The 20 catalogued workflows are documented in
[continental-lz](continental-lz.md) (4), [continental-transit](continental-transit.md)
(15), and [full-pipeline](full-pipeline.md) (1). This spec covers only how they are
registered, indexed, and submitted.

## Cache / output

Not applicable — the tooling submits work and reads a local YAML manifest; it
produces no cache/output of its own. Pipeline outputs are upstream (fwh_osm) and
covered in the per-pipeline specs.

## Gotchas & notes

- **`facets: []` is intentional, not an omission.** A reviewer expecting a facet
  index here should look at fwh_osm's `osm_geocoder/catalog.yaml`.
- **Entry-point group is `facetwork.domains`, key `osm-lz` → `osm_lz:domain`** (a
  `DomainPackage`). Some prose in `README.md` / `USER_GUIDE.md` / `CLAUDE.md` still
  refers to the older `facetwork.examples` / `ExamplePackage` and `--example osm-lz`
  wording; the authoritative source is `pyproject.toml` + `__init__.py`, which use
  the domain package API. Either way it registers **no handlers**.
- **Keep the two catalogs in sync when adding a workflow:** add the FFL declaration,
  a `WorkflowInfo` row in `tools/_lib/workflows.py`, and a `catalog.yaml` entry (the
  manifest test checks the leaf name exists in FFL; `list/submit` read the Python
  catalog). Then re-seed.
- **`submit.sh` needs `facetwork-submit` on PATH** (i.e. facetwork installed / venv
  active) or it exits 127; it validates `--inputs` JSON early so a typo never reaches
  Mongo.
- **Namespace vs example name are decoupled on purpose** — example `osm-lz` (handler
  family) vs FFL namespace `continental.*` (workflow scope). See CLAUDE.md.

## Related specs

- [continental-lz](continental-lz.md), [continental-transit](continental-transit.md),
  [full-pipeline](full-pipeline.md) — the workflows this tooling submits.
- [shared-types](shared-types.md) — why all four FFL files ship as one `--library`
  closure.
- Repo [`CLAUDE.md`](../CLAUDE.md) / [`README.md`](../README.md) /
  [`USER_GUIDE.md`](../USER_GUIDE.md) — install, seed, and run instructions.
