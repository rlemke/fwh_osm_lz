"""Publish one region's low-zoom map into the gallery's world tree.

``osm.viz.RenderTiledMap`` writes its viewer (``index.html`` + one PMTiles per zoom
band) to ``osm-output/maps/tiled/<first-layer-stem>/`` -- a name derived from the
tiles, not the region. The gallery's world index reads ``cache/osm/maps/lz/<key>/``,
so publishing is a server-side copy to the region's key plus an "About this map"
panel injected into the page.

The panel goes in the PAGE, not only the sidecar: the gallery deliberately reads no
sidecars (an N+1 of object GETs that made it crawl), so a description living only
there is read by no one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

LZ_PREFIX = "cache/osm/maps/lz/"

PANEL = """
<style>
#about-map{position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:10000;max-width:min(430px,92vw);
 font:12.5px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
 background:rgba(255,255,255,.97);border:1px solid #c9ced8;border-radius:6px;
 box-shadow:0 2px 10px rgba(0,0,0,.18)}
#about-map summary{cursor:pointer;padding:8px 12px;font-weight:600;color:#1a2332;list-style:none}
#about-map summary::-webkit-details-marker{display:none}
#about-map summary:before{content:"\\2139\\FE0F  "}
#about-map .body{padding:0 12px 12px;color:#2b3444;max-height:70vh;overflow:auto}
#about-map h4{margin:10px 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.4px;color:#5a6478}
#about-map ul{margin:4px 0;padding-left:18px} #about-map li{margin:2px 0}
#about-map code{background:#eef1f6;padding:1px 4px;border-radius:3px;font-size:11.5px}
#about-map .warn{color:#8a4b00}
</style>
<details id="about-map">
<summary>About this map &mdash; how it was made</summary>
<div class="body">
<p><b>__TITLE__</b><br><span style="color:#5a6478">__REGION__ &middot; __WHEN__</span></p>
<h4>Pipeline</h4>
<ul>
<li><b>Logical edge graph</b> &mdash; the OSM extract's road ways are merged into
    logical edges at junctions, keeping each edge's functional class
    (motorway &rarr; trunk &rarr; primary &rarr; secondary &rarr; tertiary &rarr; unclassified).</li>
<li><b>City anchors</b> &mdash; place nodes carrying <code>population</code> are scanned
    from the same extract and thresholded per zoom (500k at z2 down to 5k at z7).</li>
<li><b>Structural Betweenness Sampling</b> &mdash; origin/destination pairs are sampled
    between anchors and each is <b>routed</b> through a GraphHopper graph built from
    this same extract. Every logical edge a route rides gets a vote, so importance
    is measured by the traffic a road would actually carry between populated
    places, not by its tag.</li>
<li><b>Bypass and ring detection</b> &mdash; edges that skirt a settlement faster than
    the route through it are flagged, so a beltway survives when the high street
    does not.</li>
<li><b>Per-cell budgets</b> &mdash; the region is tiled into h3 resolution-7 hexagons
    (~5.2 km&sup2;) and each cell gets a road-length budget per zoom, so empty country
    keeps its one road through and a dense city does not swallow the whole quota.</li>
<li><b>Selection</b> &mdash; a greedy pass spends each budget on the highest-scoring
    edges (betweenness + functional class), subject to a <b>class floor per zoom</b>:
    motorway only at z2, trunk from z3, primary z4, secondary z5, tertiary z6.
    The motorway/trunk skeleton is always taken whole and does not charge the
    budget; a backbone repair pass reconnects anything the budget cut.</li>
<li><b>Monotonic reveal</b> &mdash; each zoom is a superset of the one below, so nothing
    ever disappears as you zoom in.</li>
<li><b>Vector tiles</b> &mdash; one PMTiles archive per zoom band with its own minimum
    zoom, streamed over HTTP Range. The reveal follows the map because the lower
    classes genuinely do not exist at continental scale.</li>
</ul>
<h4>Result</h4>
<ul>__RESULT__</ul>
<h4>Not done in this run</h4>
<ul class="warn">
<li>Bypass and ring flags are computed and exported but not styled differently.</li>
<li>Layers are cumulative, so a higher zoom's tiles repeat the roads below it.</li>
</ul>
</div>
</details>
</html>"""


def dest_prefix(key: str) -> str:
    return f"{LZ_PREFIX}{key.strip('/')}/"


def publish(
    s3, bucket: str, key: str, title: str, src_prefix: str, edges: int | None = None
) -> str:
    """Copy the viewer at ``src_prefix`` to the region's gallery key; return it."""
    dst = dest_prefix(key)
    objs = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=src_prefix):
        objs += page.get("Contents", [])
    if not objs:
        raise RuntimeError(f"nothing at s3://{bucket}/{src_prefix}")
    total = 0
    html_key = None
    for o in objs:
        rel = o["Key"][len(src_prefix) :]
        if not rel or rel.endswith("/"):
            continue
        if rel == "index.html":
            html_key = o["Key"]
            continue
        if rel.endswith(".meta.json"):
            continue
        s3.copy_object(Bucket=bucket, Key=dst + rel, CopySource={"Bucket": bucket, "Key": o["Key"]})
        total += o["Size"]
    if not html_key:
        raise RuntimeError(f"no index.html under s3://{bucket}/{src_prefix}")
    html = s3.get_object(Bucket=bucket, Key=html_key)["Body"].read().decode("utf-8", "replace")
    if 'id="about-map"' in html:  # idempotent: drop an older panel
        html = html[: html.index("<style>\n#about-map")] + "</html>"
    when = datetime.now(UTC).strftime("%Y-%m-%d")
    result = f"<li><b>{edges:,}</b> logical edges selected across zooms 2-7</li>" if edges else ""
    result += f"<li>{total / 1e6:.0f} MB of streamed vector tiles, six zoom bands</li>"
    panel = (
        PANEL.replace("__TITLE__", f"{title} low-zoom road network")
        .replace("__REGION__", key)
        .replace("__WHEN__", when)
        .replace("__RESULT__", result)
    )
    html = html[: html.rindex("</html>")] + panel
    body = html.encode("utf-8")
    s3.put_object(Bucket=bucket, Key=dst + "index.html", Body=body, ContentType="text/html")
    s3.put_object(
        Bucket=bucket,
        Key=dst + "index.html.meta.json",
        Body=json.dumps(
            {
                "cache_type": "maps",
                "title": f"{title} low-zoom road network",
                "generated_at": when,
                "size_bytes": len(body),
                "extra": {"region": key, "tiles_bytes": total, "selected_edges": edges},
            },
            indent=2,
        ).encode(),
        ContentType="application/json",
    )
    return dst


def copy_existing(s3, bucket: str, src_prefix: str, key: str) -> str:
    """Re-file an already-built map (e.g. a US state from the 2026-09 batch) under
    the world tree, unchanged."""
    dst = dest_prefix(key)
    n = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=src_prefix):
        for o in page.get("Contents", []):
            rel = o["Key"][len(src_prefix) :]
            if rel and not rel.endswith("/"):
                s3.copy_object(
                    Bucket=bucket, Key=dst + rel, CopySource={"Bucket": bucket, "Key": o["Key"]}
                )
                n += 1
    if not n:
        raise RuntimeError(f"nothing at s3://{bucket}/{src_prefix}")
    return dst
