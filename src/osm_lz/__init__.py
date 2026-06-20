"""Continental-scale OSM Low-Zoom (LZ) example package — pure FFL
workflows that orchestrate fwh_osm handlers across many countries to
build LZ road maps and analyse GTFS transit feeds.

This package contributes **zero** Python handlers — it is a workflow
catalog. The actual event facets it composes
(``osm.cache.<continent>.<country>``,
``osm.cache.GraphHopper.<continent>.<country>``,
``osm.Roads.ZoomBuilder.*``, ``osm.Transit.GTFS.*``) live in the
sibling `fwh_osm <https://github.com/rlemke/fwh_osm>`_ package. Install
both side by side::

    pip install -e ~/fw_handlers/fwh_osm        # the handlers
    pip install -e ~/fw_handlers/fwh_osm_lz     # this workflow catalog

Discovered by the Facetwork runner via the ``facetwork.domains`` entry
point declared in ``pyproject.toml``::

    [project.entry-points."facetwork.domains"]
    osm-lz = "osm_lz:domain"
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from facetwork.domains import DomainPackage


def _register_no_handlers(_runner: Any) -> None:
    """No-op handler registrar.

    osm-lz contributes only FFL workflows — the handlers it composes
    are registered by the fwh_osm package. Discovery still calls this
    so the example surfaces in `--example osm-lz`; it just doesn't
    add any rows to ``handler_registrations``.
    """


domain = DomainPackage(
    name="osm-lz",
    ffl_dir=Path(__file__).parent / "ffl",
    register_handlers=_register_no_handlers,
)
