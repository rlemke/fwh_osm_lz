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


# --- routed cities -----------------------------------------------------------
#
# The zoom builder routes between CITY ANCHORS: for each zoom it takes the places
# at or above that zoom's population threshold, largest first, up to the zoom's
# target count (osm_geocoder.handlers.roads.zoom_sbs.build_anchors). Replaying
# that rule over the run's own cities.geojson gives exactly the places routes
# were sampled between, and the zoom each one FIRST anchored at -- its tier.
# ``keep`` is the builder's settlement test: admin areas (state/county/country
# centroids) carry a population but are not routed to.


def anchor_rules() -> tuple[dict[int, int], dict[int, int], object]:
    """(population threshold, target count, settlement test), from the builder
    itself so the dots cannot drift from what was routed."""
    from osm_geocoder.handlers.roads.zoom_sbs import (
        ANCHOR_POP_THRESHOLDS,
        ANCHOR_TARGETS,
        is_settlement,
    )

    return dict(ANCHOR_POP_THRESHOLDS), dict(ANCHOR_TARGETS), is_settlement


def _pop(props: dict) -> int:
    p = props.get("population", 0)
    try:
        return int(p)
    except (TypeError, ValueError):
        return 0


def routed_cities(fc: dict, thresholds: dict[int, int], targets: dict[int, int], keep=None) -> dict:
    """The FeatureCollection of places routes were sampled between, each carrying
    ``tier`` (the first zoom it anchored at), ``name``, ``place``, ``population``."""
    feats = [
        f
        for f in fc.get("features", [])
        if len((f.get("geometry") or {}).get("coordinates") or []) >= 2
        and (keep is None or keep(f.get("properties") or {}))
    ]
    feats.sort(key=lambda f: _pop(f.get("properties") or {}), reverse=True)
    tier: dict[int, int] = {}
    for z in sorted(thresholds):
        picked = [
            i for i, f in enumerate(feats) if _pop(f.get("properties") or {}) >= thresholds[z]
        ]
        for i in picked[: targets.get(z, len(picked))]:
            tier.setdefault(i, z)
    out = []
    for i, z in sorted(tier.items()):
        p = feats[i].get("properties") or {}
        lon, lat = feats[i]["geometry"]["coordinates"][:2]
        out.append(
            {
                "type": "Feature",
                "properties": {
                    "name": p.get("name") or "(unnamed)",
                    "place": p.get("place") or "",
                    "population": _pop(p),
                    "tier": z,
                },
                "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
            }
        )
    return {"type": "FeatureCollection", "features": out}


_CITIES_BEGIN = "<!-- fw:cities -->"
_CITIES_END = "<!-- /fw:cities -->"

# Dots coloured by the zoom a place first anchored routing at (z2 = the biggest
# cities), sized by population; click for name and population. Built with
# textContent, never innerHTML: names are OSM data.
CITIES_JS = """<!-- fw:cities -->
<style>
.fw-city-pop{font:12.5px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#1a2332}
.fw-city-pop b{font-size:14px} .fw-city-pop .m{color:#5a6478}
#legend .ctier{display:flex;flex-wrap:wrap;gap:3px 8px;margin:2px 0 2px 20px;font-size:11px;color:#bbb}
#legend .ctier span{display:inline-flex;align-items:center;gap:3px}
#legend .ctier i{width:9px;height:9px;border-radius:50%;border:1px solid #111;display:inline-block}
</style>
<script>
(function(){
 var BUILT=__BUILT__;
 var TIERS=[[2,'#ffffff','500k+'],[3,'#ffe066','200k+'],[4,'#ffa94d','80k+'],[5,'#f06595','30k+'],[6,'#9775fa','10k+'],[7,'#4dabf7','5k+']];
 var color=['match',['get','tier']];TIERS.forEach(function(t){color.push(t[0],t[1]);});color.push('#cccccc');
 if(BUILT){var sm=document.querySelector('#title small');
   if(sm){var s=document.createElement('span');s.textContent=' \\u00b7 built '+BUILT;sm.appendChild(s);}}
 var lg=document.getElementById('legend');
 if(lg){var row=document.createElement('label');row.className='row';
   var cb=document.createElement('input');cb.type='checkbox';cb.className='lyr';cb.checked=true;cb.setAttribute('data-layer','fwcities');
   var sw=document.createElement('span');sw.className='sw';sw.style.cssText='background:#f06595;border-radius:50%';
   var tx=document.createElement('span');tx.id='fw-city-label';tx.textContent='routed cities';
   row.appendChild(cb);row.appendChild(sw);row.appendChild(tx);
   var key=document.createElement('div');key.className='ctier';
   TIERS.forEach(function(t){var e=document.createElement('span');var d=document.createElement('i');d.style.background=t[1];
     e.appendChild(d);e.appendChild(document.createTextNode('z'+t[0]+' '+t[2]));key.appendChild(e);});
   var btns=lg.querySelector('.lyrbtns');lg.insertBefore(row,btns);lg.insertBefore(key,btns);
   cb.addEventListener('change',function(){applyLayer('fwcities',cb.checked);});}
 LAYER_IDS.fwcities=['fwcities'];
 var data=null;
 function add(){
   if(!data||map.getSource('fwcities'))return;
   map.addSource('fwcities',{type:'geojson',data:data});
   map.addLayer({id:'fwcities',type:'circle',source:'fwcities',
     paint:{'circle-color':color,
       'circle-radius':['interpolate',['linear'],['zoom'],3,['interpolate',['linear'],['sqrt',['get','population']],70,2,1000,7],
                                                     9,['interpolate',['linear'],['sqrt',['get','population']],70,4,1000,12]],
       'circle-stroke-color':'#111','circle-stroke-width':1,'circle-opacity':0.92},
     layout:{'circle-sort-key':['get','population']}});
   syncLayers();
 }
 fetch(here+'cities.geojson').then(function(r){if(!r.ok)throw new Error('cities.geojson '+r.status);return r.json();})
  .then(function(j){data=j;var l=document.getElementById('fw-city-label');
     if(l)l.textContent='routed cities ('+j.features.length.toLocaleString()+')';
     if(map.isStyleLoaded())add();else map.once('load',add);})
  .catch(function(e){showErr('city dots: '+e.message);});
 map.on('styledata',function(){if(data&&!map.getSource('fwcities'))add();});
 map.on('mouseenter','fwcities',function(){map.getCanvas().style.cursor='pointer';});
 map.on('mouseleave','fwcities',function(){map.getCanvas().style.cursor='';});
 map.on('click','fwcities',function(e){
   var f=e.features&&e.features[0];if(!f)return;var p=f.properties;
   var box=document.createElement('div');box.className='fw-city-pop';
   var b=document.createElement('b');b.textContent=p.name;box.appendChild(b);
   function line(t,cls){var d=document.createElement('div');if(cls)d.className=cls;d.textContent=t;box.appendChild(d);}
   line('Population: '+Number(p.population).toLocaleString());
   if(p.place)line('OSM place: '+p.place,'m');
   line('Routing anchor from road zoom z'+p.tier,'m');
   new maplibregl.Popup({offset:8}).setLngLat(f.geometry.coordinates).setDOMContent(box).addTo(map);
 });
})();
</script>
<!-- /fw:cities -->
"""


def _inject(html: str, begin: str, end: str, block: str) -> str:
    """Put ``block`` before </body>, replacing an earlier copy -- never stacking."""
    if begin in html:
        a = html.index(begin)
        b = html.index(end, a) + len(end)
        html = html[:a] + html[b:].lstrip("\n")
    if "</body>" not in html:
        raise ValueError("viewer page has no </body> to inject before")
    i = html.rindex("</body>")
    return html[:i] + block + html[i:]


def inject_cities(html: str, built: str | None = None) -> str:
    """Add the routed-city dot layer to a tiled viewer page. Idempotent."""
    return _inject(
        html, _CITIES_BEGIN, _CITIES_END, CITIES_JS.replace("__BUILT__", json.dumps(built or ""))
    )


# --- routed vs name/kind -----------------------------------------------------
#
# Each zoom band's legend row becomes two checkboxes. "routed" = edges sampled
# routes rode at the zoom the edge was revealed at (the `routed` attribute
# osm_geocoder's zoom export writes); "name/kind" = everything else -- admitted
# by class, the motorway/trunk skeleton, backbone repair, the sparse-cell top-up,
# or as part of a corridor whose other edges were routed. Both ticked = no
# filter, i.e. the band exactly as it drew before. The band's own label still
# toggles both. Only injected when every band's tiles carry the attribute: on
# tiles without it, "routed" would silently show nothing.

_SPLIT_BEGIN = "<!-- fw:route-split -->"
_SPLIT_END = "<!-- /fw:route-split -->"

SPLIT_JS = """<!-- fw:route-split -->
<style>
#legend .rsplit{display:flex;gap:10px;margin:0 0 3px 20px;font-size:11px;color:#bbb}
#legend .rsplit label{display:inline-flex;align-items:center;gap:3px;cursor:pointer}
#legend .rsplit label:hover{color:#fff}
#legend .rsplit input{margin:0;width:11px;height:11px;accent-color:#9ecbff;cursor:pointer}
</style>
<script>
(function(){
 var st={};
 var PARTS=[['r','routed','sampled routes between cities rode this road at this zoom'],
            ['o','name/kind','admitted by road class or name: motorway/trunk skeleton, class score, backbone repair, rural top-up, or the rest of a routed corridor']];
 function filt(s){if(s.r&&s.o)return null;return s.r?['==',['get','routed'],true]:['!=',['get','routed'],true];}
 // Set only what differs. Every set fires 'styledata', and the page re-applies
 // the legend ON 'styledata' -- so an unconditional setFilter(null) on a layer
 // whose filter is undefined (MapLibre does not count those equal) re-fires it
 // forever: the style never finishes loading and no road is ever drawn.
 function applyBand(src){var s=st[src];(LAYER_IDS[src]||[]).forEach(function(id){
   if(!map.getLayer(id))return;
   var v=(s.r||s.o)?'visible':'none';
   if((map.getLayoutProperty(id,'visibility')||'visible')!==v)map.setLayoutProperty(id,'visibility',v);
   var f=filt(s);
   if(JSON.stringify(map.getFilter(id)||null)!==JSON.stringify(f))map.setFilter(id,f);});}
 var orig=applyLayer;
 applyLayer=function(src,on){
   var m=/^(.*):(r|o)$/.exec(src);
   if(m&&st[m[1]]){st[m[1]][m[2]]=on;applyBand(m[1]);return;}
   if(st[src]){st[src].r=st[src].o=on;applyBand(src);return;}
   orig(src,on);};
 document.querySelectorAll('#legend input.lyr').forEach(function(cb){
   var src=cb.getAttribute('data-layer');
   if(!/^layer[0-9]+$/.test(src))return;
   st[src]={r:cb.checked,o:cb.checked};
   cb.classList.remove('lyr');cb.style.display='none';
   var row=cb.parentNode,sub=document.createElement('div'),subs=[];
   sub.className='rsplit';
   PARTS.forEach(function(p){
     var l=document.createElement('label');l.title=p[2];
     var c=document.createElement('input');c.type='checkbox';c.className='lyr';c.checked=cb.checked;
     c.setAttribute('data-layer',src+':'+p[0]);
     c.addEventListener('change',function(){applyLayer(src+':'+p[0],c.checked);});
     l.appendChild(c);l.appendChild(document.createTextNode(p[1]));sub.appendChild(l);subs.push(c);});
   // the band's own label (its hidden box) toggles both halves
   cb.addEventListener('change',function(){subs.forEach(function(c){c.checked=cb.checked;});applyLayer(src,cb.checked);});
   row.parentNode.insertBefore(sub,row.nextSibling);
 });
 syncLayers();
})();
</script>
<!-- /fw:route-split -->
"""


def inject_route_split(html: str) -> str:
    """Split each zoom band's legend row into routed / name-kind. Idempotent."""
    return _inject(html, _SPLIT_BEGIN, _SPLIT_END, SPLIT_JS)


def pmtiles_metadata(read_range) -> dict:
    """A PMTiles v3 archive's JSON metadata, given ``read_range(offset, length)``.

    Reads only the 127-byte header and the metadata block, so checking a 36 MB
    archive costs two small range GETs."""
    import gzip
    import struct

    head = read_range(0, 127)
    if head[:7] != b"PMTiles" or head[7] != 3:
        raise ValueError("not a PMTiles v3 archive")
    meta_off, meta_len = struct.unpack_from("<QQ", head, 24)
    raw = read_range(meta_off, meta_len)
    if head[97] == 2:  # internal compression: gzip
        raw = gzip.decompress(raw)
    return json.loads(raw)


def tiles_fields(meta: dict) -> set[str]:
    return {f for layer in meta.get("vector_layers") or [] for f in (layer.get("fields") or {})}


def s3_range_reader(s3, bucket: str, key: str):
    def read(off: int, n: int) -> bytes:
        r = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes={off}-{off + n - 1}")
        return r["Body"].read()

    return read


def bands_have_routed(s3, bucket: str, keys: list[str]) -> bool:
    """True when there are band archives and EVERY one carries `routed`."""
    return bool(keys) and all(
        "routed" in tiles_fields(pmtiles_metadata(s3_range_reader(s3, bucket, k))) for k in keys
    )


def publish(
    s3,
    bucket: str,
    key: str,
    title: str,
    src_prefix: str,
    edges: int | None = None,
    cities: dict | None = None,
    built: str | None = None,
) -> str:
    """Copy the viewer at ``src_prefix`` to the region's gallery key; return it.

    ``cities`` is the routed-city FeatureCollection (see :func:`routed_cities`);
    given, it is stored beside the page and drawn as clickable dots. ``built`` is
    the date the map was BUILT (YYYY-MM-DD) -- not the date of this copy, which
    for a re-filed map can be weeks later. Defaults to today."""
    dst = dest_prefix(key)
    objs = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=src_prefix):
        objs += page.get("Contents", [])
    if not objs:
        raise RuntimeError(f"nothing at s3://{bucket}/{src_prefix}")
    total = 0
    html_key = None
    bands: list[str] = []
    for o in objs:
        rel = o["Key"][len(src_prefix) :]
        if not rel or rel.endswith("/"):
            continue
        if rel == "index.html":
            html_key = o["Key"]
            continue
        if rel.endswith(".meta.json") or rel == "cities.geojson":
            continue
        s3.copy_object(Bucket=bucket, Key=dst + rel, CopySource={"Bucket": bucket, "Key": o["Key"]})
        total += o["Size"]
        if rel.endswith(".pmtiles"):
            bands.append(dst + rel)
    if not html_key:
        raise RuntimeError(f"no index.html under s3://{bucket}/{src_prefix}")
    html = s3.get_object(Bucket=bucket, Key=html_key)["Body"].read().decode("utf-8", "replace")
    if 'id="about-map"' in html:  # idempotent: drop an older panel
        html = html[: html.index("<style>\n#about-map")] + "</html>"
    when = built or datetime.now(UTC).strftime("%Y-%m-%d")
    result = f"<li><b>{edges:,}</b> logical edges selected across zooms 2-7</li>" if edges else ""
    result += f"<li>{total / 1e6:.0f} MB of streamed vector tiles, six zoom bands</li>"
    n_cities = None
    if cities is not None:
        n_cities = len(cities.get("features", []))
        s3.put_object(
            Bucket=bucket,
            Key=dst + "cities.geojson",
            Body=json.dumps(cities, separators=(",", ":")).encode(),
            ContentType="application/geo+json",
        )
        html = inject_cities(html, when)
        result += (
            f"<li><b>{n_cities:,}</b> cities routes were sampled between, drawn as dots "
            "coloured by the zoom they first anchored at &mdash; click one for its "
            "name and population</li>"
        )
    # Only the bands this page draws: a source prefix can also hold archives an
    # earlier run left under other names, which would never carry `routed`.
    split = bands_have_routed(s3, bucket, [b for b in bands if b.rsplit("/", 1)[-1] in html])
    if split:
        html = inject_route_split(html)
        result += (
            "<li>Each zoom band splits into <b>routed</b> (roads sampled routes rode at "
            "that zoom) and <b>name/kind</b> (admitted by class or name: the "
            "motorway/trunk skeleton, class score, backbone repair, rural top-up, or "
            "the rest of a routed corridor). Both ticked is the full band.</li>"
        )
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
                "extra": {
                    "region": key,
                    "tiles_bytes": total,
                    "selected_edges": edges,
                    "routed_cities": n_cities,
                    "route_split": split,
                },
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
