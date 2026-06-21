"""Conftest for osm-lz tests.

osm-lz is a pure-FFL workflow catalog whose workflows dispatch to event facets
defined in the sibling ``fwh_osm`` package, so the FFL compile test needs that
package's ``*.ffl`` as libraries. This conftest locates fwh_osm's shipped FFL
in a layout-independent way and exposes it as the ``fwh_osm_ffl`` fixture:

  1. the importable ``osm_geocoder`` package (preferred — tracks what's installed),
  2. else the ``FWH_OSM_DIR`` env var pointing at the fwh_osm checkout,
  3. else the conventional sibling checkout ``~/fw_handlers/fwh_osm``.

If none resolve, the fixture is an empty list and the dependent tests skip,
rather than failing for an environment reason.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _osm_geocoder_pkg_dir() -> Path | None:
    try:
        import osm_geocoder  # type: ignore

        return Path(osm_geocoder.__file__).resolve().parent
    except Exception:
        return None


def _fwh_osm_ffl_root() -> Path | None:
    """Best-effort locate the directory holding fwh_osm's shipped FFL."""
    pkg = _osm_geocoder_pkg_dir()
    if pkg is not None and pkg.is_dir():
        return pkg

    env = os.environ.get("FWH_OSM_DIR")
    candidates = []
    if env:
        candidates.append(Path(env).expanduser() / "src" / "osm_geocoder")
        candidates.append(Path(env).expanduser())
    candidates.append(Path.home() / "fw_handlers" / "fwh_osm" / "src" / "osm_geocoder")
    for c in candidates:
        if c.is_dir() and any(c.rglob("*.ffl")):
            return c
    return None


@pytest.fixture(scope="session")
def fwh_osm_ffl() -> list[Path]:
    """All shipped ``*.ffl`` files from the fwh_osm package (tests excluded)."""
    root = _fwh_osm_ffl_root()
    if root is None:
        return []
    return sorted(p for p in root.rglob("*.ffl") if "/tests/" not in str(p))
