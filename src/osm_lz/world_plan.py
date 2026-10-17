"""Plan the world low-zoom run: which extracts become one map each.

The rule the run follows, stated once:

- Every continent is split into its countries, using the country extracts in
  the object store (``<continent>/<country>-latest.osm.pbf``).
- A country at or under ``max_mb`` is ONE map.
- A bigger one is broken into its sub-regions (``<continent>/<country>/<region>``),
  recursively, by the same rule. Where a too-big region has no sub-regions in the
  store yet, the plan says so (``needs_cut``) and the driver cuts them first.
- A region that ALREADY has a finished map is a leaf whatever its size: the
  cutoff is a prediction of what will fit, and a built map has settled that.

Pure: no I/O. The caller supplies the extract sizes and the set of finished maps,
which is what makes the rule testable without a fleet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Run order: smallest continent first, so a systematic failure costs least.
CONTINENT_ORDER = [
    "central-america",
    "australia-oceania",
    "africa",
    "south-america",
    "russia",
    "asia",
    "europe",
    "north-america",
]

#: Keys the store carries twice under different names, or that overlap another
#: entry. Each maps to WHY it is left out, so the plan can say so.
SKIP: dict[str, str] = {
    "africa/congo-kinshasa": "same country as africa/democratic-republic-of-the-congo",
    "africa/swaziland": "same country as africa/eswatini",
    "africa/the-gambia": "same country as africa/gambia",
    "africa/france-taaf": "listed under australia-oceania",
    "asia/israel": "covered by asia/israel-and-palestine",
    "asia/palestine": "covered by asia/israel-and-palestine",
    "asia/israel-west-bank": "covered by asia/israel-and-palestine",
    "australia-oceania/kiribati-east": "covered by australia-oceania/kiribati",
    "australia-oceania/kiribati-west": "covered by australia-oceania/kiribati",
    "australia-oceania/micronesia": "same as australia-oceania/federated-states-of-micronesia",
    "australia-oceania/pitcairn": "same as australia-oceania/pitcairn-islands",
    "australia-oceania/tokelau": "no roads to map (2026-10-05: every zoom layer was empty)",
    "central-america/mexico": "mapped under north-america/mexico",
    "south-america/south-georgia-and-south-sandwich": "no roads to map (2026-10-16: every zoom layer was empty)",
    "central-america/usa-virgin-islands": "same as central-america/united-states-virgin-islands",
    "central-america/caribbean": "an overlapping multi-island extract",
    "europe/czech-republic": "same country as europe/czechia",
    "europe/great-britain": "covered by europe/united-kingdom",
    "europe/guernesey": "same as europe/guernsey",
    "north-america/us-midwest": "mapped as the US states (north-america/us)",
    "north-america/us-northeast": "mapped as the US states (north-america/us)",
    "north-america/us-south": "mapped as the US states (north-america/us)",
    "north-america/us-west": "mapped as the US states (north-america/us)",
    "north-america/us-major-roads": (
        "a routing extract (lower 48, motorway..secondary only) for the one-graph US "
        "map at north-america/us-z2-z4, not a region"
    ),
    "south-america/falkland": "same as south-america/falkland-islands",
}

#: Countries whose sub-regions exist in the store at MORE than one tier under
#: the same prefix. Germany's 415 children are its 16 states AND ~399 districts,
#: keyed side by side, so "all children" would map the country twice. Only the
#: listed tier is used; a too-big member is cut further beneath its own key.
TIER: dict[str, list[str]] = {
    "europe/germany": [
        "baden-wuerttemberg",
        "bayern",
        "berlin",
        "brandenburg",
        "bremen",
        "hamburg",
        "hessen",
        "mecklenburg-vorpommern",
        "niedersachsen",
        "nordrhein-westfalen",
        "rheinland-pfalz",
        "saarland",
        "sachsen",
        "sachsen-anhalt",
        "schleswig-holstein",
        "thueringen",
    ],
}

#: Countries with no extract of their own whose regions ARE in the store.
SYNTHETIC: dict[str, str] = {"north-america/us": "United States"}

#: Extracts this small hold no road network worth a map (uninhabited territory).
MIN_BYTES = 100_000

_SMALL = {"and", "of", "the", "et", "da", "de", "du", "la", "le", "y"}
_LABELS = {"us": "United States", "uk": "United Kingdom"}


def label(key: str) -> str:
    """Human name for a key's last segment: ``bosnia-and-herzegovina`` ->
    ``Bosnia and Herzegovina``."""
    seg = key.rstrip("/").split("/")[-1]
    if seg in _LABELS:
        return _LABELS[seg]
    words = seg.split("-")
    return " ".join(
        w if (i and w in _SMALL) else w[:1].upper() + w[1:] for i, w in enumerate(words)
    )


def slug(key: str) -> str:
    """Unique, filesystem-safe name for a region: tile names carry it, and two
    regions sharing a slug overwrite each other's tiles."""
    return key.strip("/").replace("/", "-")


def cut_level(key: str) -> int:
    """OSM admin_level to cut a too-big region into: a country -> its states or
    provinces (4); a state -> its counties or districts (6); deeper -> 8."""
    depth = key.count("/")
    return {1: 4, 2: 6}.get(depth, 8)


@dataclass
class Node:
    key: str
    mb: float
    kind: str  # "map" | "split" | "needs_cut" | "skip"
    reason: str = ""
    children: list[str] = field(default_factory=list)
    reused: bool = False


def _children(key: str, sizes: dict[str, int]) -> list[str]:
    depth = key.count("/") + 1
    kids = sorted(k for k in sizes if k.startswith(key + "/") and k.count("/") == depth)
    if key in TIER:
        allowed = {f"{key}/{n}" for n in TIER[key]}
        kids = [k for k in kids if k in allowed]
    return kids


def plan(
    sizes: dict[str, int],
    *,
    max_mb: float = 400.0,
    done: set[str] | frozenset[str] = frozenset(),
    force: dict[str, str] | None = None,
    continents: list[str] | None = None,
) -> dict[str, Node]:
    """Every node of the world tree, keyed by extract key (continents included).

    ``sizes``: extract key -> bytes (``<key>-latest.osm.pbf`` in the store).
    ``done``: keys that already have a finished map.
    ``force``: key -> why it is mapped whole despite its size (a cut that
    produced no sub-regions: one big map is attempted rather than none).
    """
    force = force or {}
    nodes: dict[str, Node] = {}

    def visit(key: str) -> None:
        size = sizes.get(key)
        mb = (size or 0) / 1e6
        if key in SKIP:
            nodes[key] = Node(key, mb, "skip", SKIP[key])
            return
        if key in done:
            nodes[key] = Node(key, mb, "map", "a finished map already exists", reused=True)
            return
        if key in force:
            nodes[key] = Node(key, mb, "map", force[key])
            return
        if size is not None and size < MIN_BYTES:
            nodes[key] = Node(key, mb, "skip", f"extract is {size:,} bytes - no road network")
            return
        if size is not None and mb <= max_mb:
            nodes[key] = Node(key, mb, "map")
            return
        kids = _children(key, sizes)
        if kids:
            node = Node(key, mb, "split", children=kids)
            if size is None:
                node.reason = "no extract of its own; mapped by its regions"
            else:
                node.reason = f"{mb:,.0f} MB is over {max_mb:,.0f} MB"
            nodes[key] = node
            for k in kids:
                visit(k)
            return
        if size is None:
            return  # nothing in the store at all
        nodes[key] = Node(
            key,
            mb,
            "needs_cut",
            f"{mb:,.0f} MB is over {max_mb:,.0f} MB and has no sub-regions yet "
            f"(cut at admin_level {cut_level(key)})",
        )

    for cont in continents or CONTINENT_ORDER:
        countries = _children(cont, sizes)
        for syn in SYNTHETIC:
            if syn.startswith(cont + "/") and syn not in countries and _children(syn, sizes):
                countries.append(syn)
        nodes[cont] = Node(
            cont, sum(sizes.get(c, 0) for c in countries) / 1e6, "split", children=sorted(countries)
        )
        for c in sorted(countries):
            visit(c)
    return nodes


def leaves(nodes: dict[str, Node], continent: str) -> list[Node]:
    """The maps to build under ``continent``, depth-first in key order."""
    out: list[Node] = []

    def walk(key: str) -> None:
        n = nodes.get(key)
        if n is None:
            return
        if n.kind == "map":
            out.append(n)
        for c in n.children:
            walk(c)

    walk(continent)
    return out


def needing_cut(nodes: dict[str, Node], continent: str) -> list[Node]:
    return [
        n
        for k, n in sorted(nodes.items())
        if n.kind == "needs_cut" and (k == continent or k.startswith(continent + "/"))
    ]


def estimate_minutes(mb: float) -> float:
    """Map build time, fitted over the 49 US states built 2026-09
    (``0.010 x MB^1.46``; within ~20%, over-predicts at the top)."""
    return 0.010 * max(mb, 1.0) ** 1.46


def estimate_memory_gb(mb: float, graph_mb: float = 0.0) -> int:
    """Peak memory of a region's zoom-layer build, GB, rounded up with headroom.

    Used as a claim FLOOR, so it errs high -- a floor too low lets the step stack
    with other work and be OOM-killed; one too high only makes it wait.

    Two size models, and the floor is the HIGHER:

    * PBF size -- Washington 392 MB -> 10.6 GB, California 1,449 MB -> 22-25 GB
      (OOM): ~10.6 x (MB/392)^0.63.
    * Routing-graph size (the car graph built before the map) -- same two
      points at 177 MB and 508 MB of graph: ~10.6 x (graph/177)^0.78.

    The PBF alone under-predicts road-dense regions with few buildings: Minas
    Gerais (279 MB PBF) got 11 and was OOM-killed five times at 11.8-15.7 GB,
    while its graph (200 MB) is bigger than Washington's. Neither model sees
    the city count (routing pairs; Minas Gerais has 606), so the driver also
    escalates the floor after a failed attempt -- see ``escalate_memory_gb``.
    """
    import math

    by_pbf = 10.6 * (max(mb, 1.0) / 392.0) ** 0.63
    by_graph = 10.6 * (graph_mb / 177.0) ** 0.78 if graph_mb > 0 else 0.0
    return max(2, math.ceil(max(by_pbf, by_graph) + 1.5))


def escalate_memory_gb(prior_gb: int, cap_gb: int) -> int:
    """Floor for a retry after a FAILED map attempt: +35%, capped.

    A failed zoom build is almost always an OOM kill (the runner goes silent,
    the task is reclaimed, retries exhaust and it dead-letters -- no error
    text). Retrying at the same floor repeats it. Only for ``failed``: a
    ``timeout`` usually means the task sat waiting for free memory, and a
    higher floor would make it wait longer.
    """
    import math

    return min(cap_gb, max(prior_gb + 2, math.ceil(prior_gb * 1.35)))
