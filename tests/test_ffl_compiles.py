"""Offline compile check for every shipped FFL file in the osm-lz package.

osm-lz is a **pure-FFL workflow catalog**: its workflows compose event facets
and schemas that are defined in the sibling ``fwh_osm`` package, so none of its
``*.ffl`` files are self-contained. Each file is therefore compiled as the
*primary* source with **both** the package's own other FFL files **and the whole
fwh_osm FFL library** supplied as libraries (the dependency closure). That
mirrors how the runtime resolves these workflows against the deployed library,
so a clean result means the file is genuinely deployable.

The test is parametrized over the files so a failure names the offending file.
It is fully offline and in-process: it uses the Facetwork compiler directly
(``FFLParser`` + ``validate`` + ``emit_dict``) — no MongoDB, no network, no
external binaries. It requires fwh_osm's FFL (via the ``fwh_osm_ffl`` fixture in
conftest); if that is unavailable the dependent assertions skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from facetwork.emitter import emit_dict
from facetwork.parser import FFLParser
from facetwork.source import CompilerInput, FileOrigin, SourceEntry
from facetwork.validator import validate

# ---------------------------------------------------------------------------
# Locate the package's shipped FFL via the importable package, so the test
# tracks whatever is actually installed/checked out.
# ---------------------------------------------------------------------------
try:
    import osm_lz

    _PKG_DIR = Path(osm_lz.__file__).resolve().parent
except Exception:  # pragma: no cover - package must be importable to test it
    _PKG_DIR = None


def _discover_ffl() -> list[Path]:
    """All shipped ``*.ffl`` files under the package (test fixtures excluded)."""
    if _PKG_DIR is None or not _PKG_DIR.is_dir():
        return []
    return sorted(p for p in _PKG_DIR.rglob("*.ffl") if "/tests/" not in str(p))


_FFL_FILES = _discover_ffl()
_FFL_IDS = [str(p.relative_to(_PKG_DIR)) for p in _FFL_FILES] if _PKG_DIR else []


def _entry(path: Path, *, is_library: bool) -> SourceEntry:
    return SourceEntry(
        text=path.read_text(encoding="utf-8"),
        origin=FileOrigin(path=str(path)),
        is_library=is_library,
    )


def _compile_with_closure(primary: Path, library: list[Path]) -> dict:
    """Parse + validate + emit ``primary`` with ``library`` as supporting sources.

    Raises ``AssertionError`` with the first validation error if validation
    fails; returns the emitted program dict on success.
    """
    compiler_input = CompilerInput(
        primary_sources=[_entry(primary, is_library=False)],
        library_sources=[_entry(p, is_library=True) for p in library],
    )

    parser = FFLParser()
    program_ast, _registry = parser.parse_sources(compiler_input)

    result = validate(program_ast)
    assert not result.errors, "validation errors: " + "; ".join(
        str(e) for e in result.errors
    )

    emitted = emit_dict(program_ast, include_locations=False)
    assert emitted.get("type") == "Program"
    return emitted


def test_ffl_files_discovered():
    """Sanity: we found the package and a non-trivial set of FFL files."""
    assert _PKG_DIR is not None, "osm_lz package is not importable"
    assert _FFL_FILES, f"no *.ffl files discovered under {_PKG_DIR}"


@pytest.mark.parametrize("ffl_path", _FFL_FILES, ids=_FFL_IDS)
def test_ffl_compiles_clean(ffl_path: Path, fwh_osm_ffl: list[Path]):
    """The given FFL file parses, validates, and emits against the full library.

    The library closure is the package's *other* FFL plus the entire fwh_osm FFL
    library (the facets/schemas these workflows dispatch to).
    """
    if not fwh_osm_ffl:
        pytest.skip(
            "fwh_osm FFL not found (install osm_geocoder, set FWH_OSM_DIR, or "
            "clone fwh_osm to ~/fw_handlers/fwh_osm) — osm-lz workflows cannot "
            "resolve without it"
        )
    library = [p for p in _FFL_FILES if p != ffl_path] + list(fwh_osm_ffl)
    _compile_with_closure(ffl_path, library)
