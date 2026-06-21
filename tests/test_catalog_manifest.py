"""Offline checks for the composability manifest (``src/osm_lz/catalog.yaml``).

Asserts the manifest loads, every workflow carries a non-empty intent summary +
tags + param_schema and is marked as an entry point, every listed qualified_name
is plausibly real (its leaf name appears as a `workflow <Leaf>` in the package's
.ffl sources), and there are no duplicate entries.

osm-lz is a **pure-FFL workflow catalog** with no event facets of its own, so its
``facets`` list is expected to be empty; the per-facet field checks still run if
any facet is ever added. No runner, DB, or network needed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from osm_lz import catalog

# Repo root: tests/ -> repo root is parents[1].
_REPO_ROOT = Path(__file__).resolve().parents[1]
_FFL_ROOT = _REPO_ROOT / "src" / "osm_lz"


def _all_ffl_text() -> str:
    parts = [p.read_text(encoding="utf-8") for p in _FFL_ROOT.rglob("*.ffl")]
    assert parts, "no .ffl sources found under src/osm_lz"
    return "\n".join(parts)


@pytest.fixture(scope="module")
def ffl_text() -> str:
    return _all_ffl_text()


@pytest.fixture(scope="module")
def manifest():
    return catalog.load_manifest()


def test_manifest_loads(manifest):
    assert isinstance(manifest, dict)
    assert manifest.get("package") == "osm-lz"
    assert isinstance(catalog.workflows(), list) and catalog.workflows()
    # osm-lz is a pure-FFL catalog: facets() is a list (expected empty).
    assert isinstance(catalog.facets(), list)


def test_workflows_have_summary_and_tags():
    for wf in catalog.workflows():
        qn = wf.get("qualified_name", "<missing>")
        summary = wf.get("summary", "")
        assert isinstance(summary, str) and summary.strip(), f"empty summary for {qn}"
        tags = wf.get("tags")
        assert isinstance(tags, list) and tags, f"empty tags for {qn}"
        assert all(isinstance(t, str) and t.strip() for t in tags), f"bad tag in {qn}"
        assert wf.get("entry_point") is True, f"workflow {qn} not marked entry_point"
        assert isinstance(wf.get("param_schema"), dict), f"no param_schema for {qn}"


def test_facets_have_required_fields():
    """No facets are expected in osm-lz, but enforce the shape of any that exist."""
    valid_effects = {"pure", "external", "io"}
    valid_costs = {"free", "cheap", "moderate", "expensive"}
    for fc in catalog.facets():
        qn = fc.get("qualified_name", "<missing>")
        assert fc.get("purpose", "").strip(), f"empty purpose for {qn}"
        assert fc.get("signature", "").strip(), f"empty signature for {qn}"
        assert fc.get("effect") in valid_effects, f"bad effect for {qn}: {fc.get('effect')}"
        assert fc.get("cost") in valid_costs, f"bad cost for {qn}: {fc.get('cost')}"
        assert fc.get("namespace"), f"no namespace for {qn}"
        assert qn.startswith(fc["namespace"] + "."), f"{qn} not under namespace {fc['namespace']}"


def test_no_duplicate_entries():
    wf_names = [w["qualified_name"] for w in catalog.workflows()]
    fc_names = [f["qualified_name"] for f in catalog.facets()]
    assert len(wf_names) == len(set(wf_names)), "duplicate workflow qualified_names"
    assert len(fc_names) == len(set(fc_names)), "duplicate facet qualified_names"
    overlap = set(wf_names) & set(fc_names)
    assert not overlap, f"name listed as both workflow and facet: {overlap}"


def test_workflow_qualified_names_are_real(ffl_text):
    """Each workflow's leaf name must be declared as a `workflow <Leaf>` in the FFL."""
    for wf in catalog.workflows():
        qn = wf["qualified_name"]
        leaf = qn.rsplit(".", 1)[-1]
        pat = re.compile(rf"\bworkflow\s+{re.escape(leaf)}\b")
        assert pat.search(ffl_text), f"no `workflow {leaf}` found in FFL for {qn}"


def test_facet_qualified_names_are_real(ffl_text):
    """Each facet's leaf name must be declared as a `facet <Leaf>` in the FFL."""
    for fc in catalog.facets():
        qn = fc["qualified_name"]
        leaf = qn.rsplit(".", 1)[-1]
        pat = re.compile(rf"\bfacet\s+{re.escape(leaf)}\b")
        assert pat.search(ffl_text), f"no `facet {leaf}` found in FFL for {qn}"
