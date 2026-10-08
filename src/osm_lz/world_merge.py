"""Merge per-region low-zoom layers into one map, without re-routing anything.

Each region's build leaves its finished zoom bands on disk (``roads_z<N>.geojson``,
cumulative) plus ``edge_importance.jsonl`` and the cities it routed between. A
country map is the concatenation of those bands, tiled once per band.

What a merge is NOT, and the page says so:

- Importance is per REGION: a road's band is the zoom its own region's
  selection admitted it at, against that region's cities. A sparse state's z2
  is its own top-city routes, not a national ranking.
- Routes stop at region borders: no pair spans two extracts.
- Regions built under different rules stay different until rebuilt.

Roads near a border are in both regions' extracts. Exact geometric duplicates
are dropped (coordinates rounded to ~1 m, direction-normalised); a road kept
from one side is ``routed`` if EITHER side's routes rode it. Partial overlaps
(an extract clipped mid-way) are not detectable this way and are drawn twice,
which looks the same.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

from osm_lz.world_retile import routed_index

BANDS = range(2, 8)


def geom_key(coords: list) -> str:
    """Direction-independent identity of a line, to ~1 m."""
    pts = [(round(c[0], 5), round(c[1], 5)) for c in coords]
    if pts and pts[-1] < pts[0]:
        pts.reverse()
    return hashlib.blake2b(json.dumps(pts).encode(), digest_size=12).hexdigest()


def region_routed(layer_dir: Path) -> dict[int, dict[int, bool]]:
    """{band: {edge_id: routed}} from a build's edge_importance.jsonl (empty if absent)."""
    p = Path(layer_dir) / "edge_importance.jsonl"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return routed_index(f)


def band_features(
    fc: dict, region: str, band: int, routed: dict[int, dict[int, bool]]
) -> Iterable[dict]:
    """A region's band features, tagged with the region and a definite ``routed``.

    Layers exported before the attribute existed get it from the same rule the
    export now applies (SBS of THIS band > 0)."""
    by_edge = routed.get(band, {})
    for f in fc.get("features", []):
        p = f.setdefault("properties", {})
        if "routed" not in p:
            p["routed"] = bool(by_edge.get(int(p.get("edge_id", -1)), False))
        p["region"] = region
        yield f


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def merge_band(
    regions: dict[str, Path], band: int, out_seq: Path, routed_by_region: dict[str, dict]
) -> dict:
    """Write one band of every region to ``out_seq`` (GeoJSONSeq); return counts.

    Two passes, so memory holds one region's band and a key -> routed map, never
    the whole country's features: pass 1 ORs ``routed`` over every copy of a
    line, pass 2 streams each line's first copy out with that value."""
    any_routed: dict[str, bool] = {}
    read = skipped = 0
    for region, d in sorted(regions.items()):
        fc = _load(Path(d) / f"roads_z{band}.geojson")
        if fc is None:
            skipped += 1
            continue
        for f in band_features(fc, region, band, routed_by_region.get(region, {})):
            read += 1
            k = geom_key(f["geometry"]["coordinates"])
            any_routed[k] = any_routed.get(k, False) or f["properties"]["routed"]
    written = routed_n = 0
    done: set[str] = set()
    with open(out_seq, "w", encoding="utf-8") as fh:
        for region, d in sorted(regions.items()):
            fc = _load(Path(d) / f"roads_z{band}.geojson")
            if fc is None:
                continue
            for f in band_features(fc, region, band, routed_by_region.get(region, {})):
                k = geom_key(f["geometry"]["coordinates"])
                if k in done or k not in any_routed:
                    continue
                done.add(k)
                f["properties"]["routed"] = any_routed[k]
                routed_n += any_routed[k]
                fh.write(json.dumps(f, separators=(",", ":")) + "\n")
                written += 1
    return {
        "band": band,
        "read": read,
        "written": written,
        "border_duplicates": read - written,
        "routed": routed_n,
        "regions_unreadable": skipped,
    }


def retarget_page(html: str, from_stem: str, to_stem: str, from_label: str, to_label: str) -> str:
    """Point a region's viewer page at the merged bands.

    The viewer names its archives and source layers after the region
    (``roads_z2_tiles_<stem>_roads_z2-z2_14.pmtiles``, ``<stem>_roads_z2``) and
    its legend after the label; everything else in it is region-independent.
    Injected blocks (cities, split, panel) are stripped -- publish re-adds them."""
    for begin, end in (
        ("<!-- fw:cities -->", "<!-- /fw:cities -->"),
        ("<!-- fw:route-split -->", "<!-- /fw:route-split -->"),
    ):
        if begin in html:
            a = html.index(begin)
            html = html[:a] + html[html.index(end, a) + len(end) :].lstrip("\n")
    if 'id="about-map"' in html:
        html = html[: html.index("<style>\n#about-map")] + "</html>"
    html = html.replace(f"{from_stem}_roads", f"{to_stem}_roads")
    html = html.replace(f"{from_stem.replace('_', '-')} roads", f"{to_label} roads")
    html = html.replace(f"{from_stem} roads", f"{to_label} roads")
    html = html.replace(from_label, to_label)
    return html
