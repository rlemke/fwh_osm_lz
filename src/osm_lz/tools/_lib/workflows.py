"""Static catalog of the workflows osm-lz ships.

Used by ``tools/list-workflows`` and ``tools/submit`` so callers don't
need to know FFL filenames or qualified workflow paths up front.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# FFL paths — every workflow this catalog ships comes from one of these files.
# Submission uses *all four* as ``--library`` to give the parser the full set
# of namespaces (continental.types is referenced by all the others, and the
# transit / lz workflows cross-reference each other in the FullPipeline).
# ---------------------------------------------------------------------------

_FFL_DIR = Path(__file__).resolve().parents[2] / "ffl"

FFL_FILES: list[Path] = [
    _FFL_DIR / "continental_types.ffl",
    _FFL_DIR / "continental_lz_workflows.ffl",
    _FFL_DIR / "continental_gtfs_workflows.ffl",
    _FFL_DIR / "continental_full.ffl",
]


# ---------------------------------------------------------------------------
# Workflow catalog.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowInfo:
    """Description of a single workflow this catalog ships.

    ``primary`` is the FFL file that *defines* the workflow — it goes
    in ``--primary`` when submitting; the others go in ``--library``.
    """

    qualified_name: str
    description: str
    primary: Path
    inputs: dict[str, str] = field(default_factory=dict)


# All paths use the package-relative resolution above so they survive
# editable installs and zip installs identically.

_TYPES = _FFL_DIR / "continental_types.ffl"
_LZ = _FFL_DIR / "continental_lz_workflows.ffl"
_GTFS = _FFL_DIR / "continental_gtfs_workflows.ffl"
_FULL = _FFL_DIR / "continental_full.ffl"


def _output_base(default: str = "/data/lz-output") -> dict[str, str]:
    return {"output_base": f"String, default {default!r}"}


_LZ_OUTPUT = _output_base()


CATALOG: list[WorkflowInfo] = [
    # ---- LZ road infrastructure ---------------------------------------
    WorkflowInfo(
        "continental.lz.BuildUSLowZoom",
        "LZ pipeline for the United States (single ~9 GB PBF region)",
        _LZ,
        _LZ_OUTPUT,
    ),
    WorkflowInfo(
        "continental.lz.BuildCanadaLowZoom",
        "LZ pipeline for Canada",
        _LZ,
        _LZ_OUTPUT,
    ),
    WorkflowInfo(
        "continental.lz.BuildEuropeLowZoom",
        "LZ pipeline for the 12 European regions (DE, FR, UK, ES, IT, …)",
        _LZ,
        _LZ_OUTPUT,
    ),
    WorkflowInfo(
        "continental.lz.BuildContinentalLZ",
        "LZ pipeline across all 14 regions (US + Canada + Europe)",
        _LZ,
        _LZ_OUTPUT,
    ),
    # ---- GTFS transit -------------------------------------------------
    *[
        WorkflowInfo(
            f"continental.transit.Analyze{agency}",
            f"GTFS feed analysis for {agency}",
            _GTFS,
        )
        for agency in (
            "Amtrak",
            "MBTA",
            "CTA",
            "MTA",
            "TransLink",
            "TTC",
            "OCTranspo",
            "DeutscheBahn",
            "SNCF",
            "Renfe",
            "Trenitalia",
        )
    ],
    WorkflowInfo(
        "continental.transit.AnalyzeUSTransit",
        "Roll-up of the four US GTFS agencies",
        _GTFS,
    ),
    WorkflowInfo(
        "continental.transit.AnalyzeCanadaTransit",
        "Roll-up of the three Canadian GTFS agencies",
        _GTFS,
    ),
    WorkflowInfo(
        "continental.transit.AnalyzeEuropeTransit",
        "Roll-up of the four European GTFS agencies",
        _GTFS,
    ),
    WorkflowInfo(
        "continental.transit.ContinentalTransitAnalysis",
        "All 11 GTFS agencies in one bundle",
        _GTFS,
    ),
    # ---- Combined pipeline -------------------------------------------
    WorkflowInfo(
        "continental.FullContinentalPipeline",
        "LZ pipeline + GTFS transit analysis combined",
        _FULL,
        _LZ_OUTPUT,
    ),
]


def find(qualified_name: str) -> WorkflowInfo | None:
    """Look a workflow up by its qualified name. Returns ``None`` on miss."""
    for w in CATALOG:
        if w.qualified_name == qualified_name:
            return w
    return None


def library_files(primary: Path) -> list[Path]:
    """Return the ``--library`` set for a given primary FFL file.

    All four FFL files this catalog ships are namespace-coupled, so we
    pass the full set minus the primary; that way the parser always
    sees ``continental.types`` (and any cross-references between
    workflow files) regardless of which workflow you submit.
    """
    return [f for f in FFL_FILES if f != primary]
