#!/usr/bin/env python
"""Merge finished per-region low-zoom maps into one map -- no routing.

    python tools/lz_merge.py --layers-root /path/to/lz-states \\
        --key north-america/us/all-states --label "United States" \\
        --template north-america/us/montana --regions-from-tree north-america/us

Reads each region's zoom bands from ``<layers-root>/<slug>/`` (the builders'
scratch output), merges and de-duplicates them per band (see osm_lz.world_merge),
tiles each band with the same tippecanoe options the region maps use, and
publishes the result into the world tree with the merged routed cities.

The region set is the published maps under ``--regions-from-tree`` (so a merge
never includes a region with no published map), or ``--regions a,b,c``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from osm_lz import world_merge as wm  # noqa: E402
from osm_lz import world_publish as pub  # noqa: E402

BUCKET = os.environ.get("FW_MAPS_BUCKET", "afl-cache")


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("FW_S3_ENDPOINT", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("FW_S3_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("FW_S3_SECRET_KEY", "minioadmin"),
    )


def tree_regions(s3, parent: str, exclude: str) -> list[str]:
    """Slugs of the published maps directly under ``parent`` in the world tree."""
    pre = f"{pub.LZ_PREFIX}{parent.strip('/')}/"
    out = set()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=pre):
        for o in page.get("Contents", []):
            rest = o["Key"][len(pre) :]
            if rest.count("/") == 1 and rest.endswith("/index.html"):
                out.add(rest.split("/")[0])
    out.discard(exclude.strip("/").split("/")[-1])
    return sorted(out)


def region_cities(d: Path, region: str) -> list[dict]:
    """The cities a region's build routed between: its own record if it wrote
    one, else a replay of the current rule over its scan."""
    rec = d / "anchor_cities.geojson"
    if rec.exists():
        feats = json.loads(rec.read_text())["features"]
    else:
        feats = pub.routed_cities(
            json.loads((d / "cities.geojson").read_text()), *pub.anchor_rules()
        )["features"]
    for f in feats:
        f["properties"]["region"] = region
    return feats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--layers-root", required=True, type=Path)
    ap.add_argument("--key", required=True, help="world-tree key to publish at")
    ap.add_argument("--label", required=True)
    ap.add_argument("--stem", default="us", help="layer/file name stem for the merged bands")
    ap.add_argument(
        "--template", required=True, help="a published region map whose viewer to reuse"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--regions-from-tree")
    g.add_argument("--regions")
    ap.add_argument("--note", action="append", default=[], help="extra caveat for the page")
    ap.add_argument("--tippecanoe", default="tippecanoe")
    a = ap.parse_args()

    s3 = s3_client()
    regions = (
        tree_regions(s3, a.regions_from_tree, a.key)
        if a.regions_from_tree
        else [r.strip() for r in a.regions.split(",") if r.strip()]
    )
    dirs = {
        r: a.layers_root / r for r in regions if (a.layers_root / r / "roads_z2.geojson").exists()
    }
    missing = sorted(set(regions) - set(dirs))
    print(
        f"{len(dirs)} regions with layers" + (f"; no layers for {missing}" if missing else ""),
        flush=True,
    )

    routed = {r: wm.region_routed(d) for r, d in dirs.items()}
    work = Path(tempfile.mkdtemp(prefix="lz-merge-"))
    staging = f"cache/osm/maps/_merge/{a.stem}/"
    stats = []
    try:
        for z in wm.BANDS:
            seq = work / f"band{z}.geojsonseq"
            st = wm.merge_band(dirs, z, seq, routed)
            name = f"roads_z{z}_tiles_{a.stem}_roads_z{z}-z{z}_14.pmtiles"
            subprocess.run(
                [
                    a.tippecanoe,
                    "-q",
                    "-o",
                    str(work / name),
                    "-Z",
                    str(z),
                    "-z",
                    "14",
                    "--force",
                    "--layer",
                    f"{a.stem}_roads_z{z}",
                    "--drop-densest-as-needed",
                    "--coalesce-densest-as-needed",
                    "--read-parallel",
                    str(seq),
                ],
                check=True,
            )
            seq.unlink()
            s3.upload_file(str(work / name), BUCKET, staging + name)
            st["tiles_mb"] = round((work / name).stat().st_size / 1e6, 1)
            (work / name).unlink()
            stats.append(st)
            print(json.dumps(st), flush=True)

        tkey = pub.dest_prefix(a.template) + "index.html"
        html = s3.get_object(Bucket=BUCKET, Key=tkey)["Body"].read().decode()
        tslug = a.template.strip("/").split("/")[-1]
        tlabel = json.loads(
            s3.get_object(Bucket=BUCKET, Key=pub.dest_prefix(a.template) + "index.html.meta.json")[
                "Body"
            ].read()
        )["title"].replace(" low-zoom road network", "")
        page = wm.retarget_page(html, tslug.replace("-", "_"), a.stem, tlabel, a.label)
        if tslug.replace("-", "_") in page or tlabel in page:
            raise SystemExit(
                f"template still names {tslug!r} after retargeting -- refusing to publish"
            )
        s3.put_object(
            Bucket=BUCKET, Key=staging + "index.html", Body=page.encode(), ContentType="text/html"
        )

        cities = {"type": "FeatureCollection", "features": []}
        for r, d in dirs.items():
            cities["features"] += region_cities(d, r)
        edges = sum(s["written"] for s in stats if s["band"] == 7)
        notes = [
            f"MERGED PREVIEW of {len(dirs)} separately built regions -- nothing was re-routed.",
            "A road's band is the zoom its OWN region's selection admitted it at, against "
            "that region's cities: importance is per region, not national.",
            "Routes stop at region borders: no city pair spans two regions.",
            f"{sum(s['border_duplicates'] for s in stats):,} border duplicates removed across "
            "all bands (exact geometry); partial overlaps are drawn twice.",
        ] + a.note
        dst = pub.publish(
            s3,
            BUCKET,
            a.key,
            a.label,
            staging,
            edges,
            cities=cities,
            built=datetime.now(UTC).strftime("%Y-%m-%d"),
            notes=notes,
        )
        print(f"published {dst} ({len(cities['features']):,} routed cities, {edges:,} z7 edges)")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
