#!/usr/bin/env python
"""Build the low-zoom road map for the world, continent by continent.

Plan (``osm_lz.world_plan``): one map per country at or under ``--max-mb``; a
bigger country is broken into its sub-regions, recursively, and sub-regions the
object store does not have yet are CUT first (``osm.planet.BuildAdminSetWorkflow``).

Per continent, in order:

1. **cut** the sub-regions a too-big region needs (a few at a time, on the fleet);
2. **graphs** -- build every leaf's routing graph (parallel across the fleet);
3. **maps** -- up to ``FW_LZ_MAPS_IN_FLIGHT`` (4) at once. graphhopper-web
   serves exactly one region, so each map gets its OWN region router
   (``fw fleet router add``, placed by the fleet-agents; the handlers find it by
   region), waits for it, runs ``BuildStateLowZoomMap``, publishes the viewer
   into the gallery's world tree (``cache/osm/maps/lz/<key>/``) and removes the
   router. Until 2026-10-15 this was one map at a time behind the single
   fleet-wide router, repointed for every map.

Everything is a submitted WORKFLOW; this only sequences them, which no FFL can
express because a router is fleet configuration, not a step.

Resumable: progress is in a ledger (``$FW_LZ_STATE_DIR``, default
``~/.facetwork/lz-world``) and a finished map is detected in the store, so the
driver can be killed and restarted. It also writes
``cache/osm/maps/lz/_status.json``, which the gallery's world index renders --
planned, in-progress and failed regions included, so a gap is never silent.

    python tools/lz_world.py --dry                      # the plan, with estimates
    python tools/lz_world.py                            # run every continent
    python tools/lz_world.py --continents central-america
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from osm_lz import world_plan as wp  # noqa: E402
from osm_lz import world_publish as pub  # noqa: E402

FW_ROOT = Path(os.environ.get("FW_ROOT") or Path.home() / "facetwork")
HANDLERS = Path(os.environ.get("FWH_HANDLERS_ROOT") or Path.home() / "fw_handlers")
OSM_FFL = HANDLERS / "fwh_osm" / "src" / "osm_geocoder" / "handlers"
PLANET_FFL = OSM_FFL / "planet" / "ffl" / "osmplanet.ffl"
LZ_FFL = HERE.parent / "src" / "osm_lz" / "ffl" / "us_states_lz.ffl"
STATE_DIR = Path(os.environ.get("FW_LZ_STATE_DIR") or Path.home() / ".facetwork" / "lz-world")
LEDGER = STATE_DIR / "ledger.json"
MAPS_BUCKET = os.environ.get("FW_MAPS_BUCKET", "afl-cache")
EXTRACTS_BUCKET = os.environ.get("FW_OSM_EXTRACT_BUCKET", "osm-extracts")
ROUTER_GROUP = os.environ.get("FW_LZ_ROUTER_GROUP", "heavy")
PROFILE = "car"
#: Where each map's zoom layers are written. Must be SHARED storage: the layers
#: are built on one host and tiled by steps any host may claim (2026-10-02: a
#: local default failed every tile step that landed elsewhere).
OUTPUT_BASE = os.environ.get("FW_LZ_OUTPUT_BASE") or f"s3://{MAPS_BUCKET}/osm-output/lz-world"

ROUTER_READY_TIMEOUT = 45 * 60
CUT_TIMEOUT = 8 * 3600
GRAPH_TIMEOUT = 10 * 3600
CUTS_IN_FLIGHT = 2
GRAPH_WIDTH = 3
GRAPH_CHUNK = 40
#: Maps built at once. Each has its OWN region router (``fw fleet router``), so
#: maps no longer queue behind one fleet-wide router being repointed (1-3 min
#: per map, ~2.5 h of the remaining world in router switches alone). Bounded by
#: router slots: heavy hosts x 2 per host, at ROUTER_REPLICAS per map.
MAPS_IN_FLIGHT = int(os.environ.get("FW_LZ_MAPS_IN_FLIGHT", "4"))
#: Two replicas: a router on a Docker Desktop Mac answers only its own host's
#: runners, so a single replica placed there would strand a map built elsewhere.
ROUTER_REPLICAS = int(os.environ.get("FW_LZ_ROUTER_REPLICAS", "2"))

_ledger_lock = threading.Lock()
#: ONE limit for every map building, adopted or new: each holds
#: ROUTER_REPLICAS router slots, so the total must stay within the fleet's.
_map_slots = threading.Semaphore(MAPS_IN_FLIGHT)
#: Regions being adopted from an earlier driver; the plan leaves them alone.
_adopting: set[str] = set()

#: Regions mapped whole although just over --max-mb: cutting them would give a
#: worse map for no memory reason (448 MB built fine whole).
MAP_WHOLE = {
    "africa/madagascar": "417 MB, one island: mapped whole (as in the first pass)",
}


def log(msg: str) -> None:
    print(f"[{datetime.now(UTC):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


# --- endpoints (from the server catalog, never a literal host) ---------------


def _catalog():
    from facetwork.servers import catalog

    return catalog


def mongo_url() -> str:
    return os.environ.get("FW_MONGODB_URL") or _catalog().resolve_url("mongodb://afl-mongodb:27017")


def s3():
    import boto3

    ep = os.environ.get("FW_S3_ENDPOINT") or "http://afl-minio:9000"
    return boto3.client(
        "s3",
        endpoint_url=_catalog().resolve_url(ep),
        aws_access_key_id=os.environ.get("FW_S3_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("FW_S3_SECRET_KEY", "minioadmin"),
    )


_DB = None
_DB_LOCK = threading.Lock()


def db():
    """ONE client for the driver's lifetime. This built a new MongoClient per
    call -- every 30 s while a map ran -- and never closed any; each holds
    sockets and monitor threads, so after ~20 h the driver died with "Too many
    open files" (2026-10-03, mid-Africa). Locked: the map threads call it at
    once."""
    global _DB
    with _DB_LOCK:
        if _DB is None:
            from pymongo import MongoClient

            _DB = MongoClient(mongo_url(), serverSelectionTimeoutMS=15000).facetwork
    return _DB


# --- ledger + status ---------------------------------------------------------


def ledger_read() -> dict:
    try:
        return json.loads(LEDGER.read_text())
    except (OSError, ValueError):
        return {}


def _settled(fn, *a) -> None:
    """Run ``fn`` under the ledger lock, retrying when another map's thread
    changed a ledger record mid-serialisation (they update their own records
    without the lock; only the WRITES are serialised)."""
    with _ledger_lock:
        for attempt in range(5):
            try:
                return fn(*a)
            except RuntimeError as e:
                if "changed size" not in str(e) or attempt == 4:
                    raise
                time.sleep(0.05)


def ledger_write(d: dict) -> None:
    _settled(_ledger_write, d)


def status_write(s3c, nodes, led, max_mb, order) -> None:
    _settled(write_status, s3c, nodes, led, max_mb, order)


def _ledger_write(d: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
    tmp.replace(LEDGER)


def _state_of(node: wp.Node, rec: dict) -> str:
    if node.kind == "skip":
        return "skipped"
    if node.kind == "split":
        return "split"
    if node.kind == "needs_cut":
        return {"running": "cutting", "failed": "failed"}.get(rec.get("cut_state", ""), "planned")
    if node.reused or rec.get("map_state") == "completed":
        return "done"
    if rec.get("map_state") == "running":
        return "building"
    if (
        rec.get("map_state") in ("failed", "terminated", "timeout")
        or rec.get("graph_state") == "failed"
    ):
        return "failed"
    return "planned"


def write_status(
    s3c, nodes: dict[str, wp.Node], led: dict, max_mb: float, order: list[str]
) -> None:
    out = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "max_mb": max_mb,
        "continents": order,
        "nodes": {},
    }
    for key, n in nodes.items():
        rec = led.get(key, {})
        out["nodes"][key] = {
            "label": wp.label(key),
            "mb": round(n.mb, 1),
            "kind": n.kind,
            "state": _state_of(n, rec),
            "reason": n.reason or rec.get("error", "")[:300],
            "children": n.children,
        }
        if rec.get("built"):
            out["nodes"][key]["built"] = rec["built"]
    s3c.put_object(
        Bucket=MAPS_BUCKET,
        Key=pub.LZ_PREFIX + "_status.json",
        Body=json.dumps(out, indent=1).encode(),
        ContentType="application/json",
    )


# --- store reads -------------------------------------------------------------


def extract_sizes(s3c, continents: list[str]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    pag = s3c.get_paginator("list_objects_v2")
    for cont in continents:
        for page in pag.paginate(Bucket=EXTRACTS_BUCKET, Prefix=cont + "/"):
            for o in page.get("Contents", []):
                k = o["Key"]
                if k.endswith("-latest.osm.pbf"):
                    sizes[k[: -len("-latest.osm.pbf")]] = o["Size"]
    return sizes


#: page meta key -> (LastModified, carries the route split)
_split_memo: dict[str, tuple[object, bool]] = {}


def finished_maps(s3c) -> set[str]:
    """Regions whose published map is CURRENT: its page carries the per-band
    routed / name-kind split (``route_split`` in the page meta), which needs
    tiles with the ``routed`` attribute.

    ⚠️ "A page exists" is not enough. The 302 maps of the first world pass
    (2026-10-02..05) predate ``routed``, so they draw no split and no city
    dots, and only a rebuild can add either. Counting them done would leave
    them like that for good. Meta reads are memoised per LastModified, so a
    re-plan costs one listing."""
    done = set()
    for page in s3c.get_paginator("list_objects_v2").paginate(
        Bucket=MAPS_BUCKET, Prefix=pub.LZ_PREFIX
    ):
        for o in page.get("Contents", []):
            k = o["Key"]
            if not k.endswith("/index.html.meta.json"):
                continue
            hit = _split_memo.get(k)
            if hit is None or hit[0] != o["LastModified"]:
                try:
                    meta = json.loads(s3c.get_object(Bucket=MAPS_BUCKET, Key=k)["Body"].read())
                    ok = bool((meta.get("extra") or {}).get("route_split"))
                except Exception:  # noqa: BLE001 - unreadable meta: rebuild it
                    ok = False
                hit = _split_memo[k] = (o["LastModified"], ok)
            if hit[1]:
                done.add(k[len(pub.LZ_PREFIX) : -len("/index.html.meta.json")])
    return done


def routed_cities_for(s3c, key: str) -> dict | None:
    """The cities this region's routes were sampled between, from the run's own
    cities.geojson (BuildZoomLayers uploads its whole work dir to the s3
    output_dir). None -- and a loud log -- when it cannot be read: the map still
    publishes, without dots, rather than failing a finished build over them."""
    base = OUTPUT_BASE.removeprefix("s3://")
    bucket, _, prefix = base.partition("/")
    d = f"{prefix.rstrip('/')}/{wp.slug(key)}/"
    # The builder's own record of what it anchored (z2/z3 floors and region
    # scaling included) -- preferred over re-deriving the rule here.
    try:
        return json.loads(
            s3c.get_object(Bucket=bucket, Key=d + "anchor_cities.geojson")["Body"].read()
        )
    except Exception:  # noqa: BLE001 - older builds have no record; replay below
        pass
    k = d + "cities.geojson"
    try:
        fc = json.loads(s3c.get_object(Bucket=bucket, Key=k)["Body"].read())
        return pub.routed_cities(fc, *pub.anchor_rules())
    except Exception as e:  # noqa: BLE001 - dots are an overlay, not the map
        log(f"    no city dots for {key}: cannot read s3://{bucket}/{k}: {e}")
        return None


def has_graph(s3c, key: str) -> bool:
    r = s3c.list_objects_v2(
        Bucket=MAPS_BUCKET, MaxKeys=1, Prefix=f"cache/osm/graphhopper/{key}-latest/{PROFILE}/"
    )
    return bool(r.get("Contents"))


# --- workflows ---------------------------------------------------------------


def _libs(exclude: Path) -> list[str]:
    out: list[str] = []
    for f in sorted(OSM_FFL.rglob("*.ffl")):
        if f.resolve() != exclude.resolve():
            out += ["--library", str(f)]
    return out


def submit(primary: Path, workflow: str, inputs: dict) -> str:
    cmd = [
        str(FW_ROOT / "fw"),
        "ffl",
        "run",
        "--primary",
        str(primary),
        *_libs(primary),
        "--workflow",
        workflow,
        "--inputs",
        json.dumps(inputs),
        # A bootstrap routes by the workflow's OWN namespace; "continental"
        # is polled by no runner. The step tasks are osm.* and route themselves.
        "--task-list",
        "osm",
    ]
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=FW_ROOT,
        env={**os.environ, "FW_MONGODB_URL": mongo_url()},
    )
    for line in r.stdout.splitlines():
        if line.strip().startswith("Runner ID:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(
        f"submit {workflow} failed (exit {r.returncode}): {r.stdout[-1500:]} {r.stderr[-1500:]}"
    )


def run_state(rid: str) -> str:
    r = db().runners.find_one({"uuid": rid}, {"state": 1})
    return (r or {}).get("state", "?")


def run_error(rid: str) -> str:
    d = db()
    run = d.runners.find_one({"uuid": rid}, {"workflow_id": 1})
    if not run:
        return ""
    # The error lives on the TASK, keyed by the run's workflow_id.
    for t in d.tasks.find(
        {"workflow_id": run["workflow_id"], "error": {"$nin": [None, "", {}]}},
        {"error": 1, "name": 1},
    ):
        e = t.get("error")
        msg = e.get("message") if isinstance(e, dict) else str(e)
        if msg:
            return f"{t.get('name', '?')}: {msg[:400]}"
    return ""


def wait(rid: str, timeout: float) -> tuple[str, str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = run_state(rid)
        if st in ("completed", "failed", "terminated"):
            return st, ("" if st == "completed" else run_error(rid))
        time.sleep(30)
    return "timeout", f"still {run_state(rid)} after {timeout / 3600:.1f} h"


def map_output(rid: str) -> tuple[str, int | None]:
    """Where RenderTiledMap's viewer landed, from the run's own step results."""
    d = db()
    run = d.runners.find_one({"uuid": rid}, {"workflow_id": 1})
    edges, base, map_path = None, "", ""
    for st in d.steps.find({"workflow_id": run["workflow_id"]}, {"facet_name": 1, "attributes": 1}):
        found = _find(st.get("attributes") or {}, ("output_path", "selected_edges", "map_path"))
        if edges is None and found.get("selected_edges") is not None:
            try:
                edges = int(found["selected_edges"])
            except (TypeError, ValueError):
                pass
        op = found.get("output_path")
        if st.get("facet_name") == "osm.viz.RenderTiledMap" and isinstance(op, str) and op:
            base = Path(op).parent.name
        mp = found.get("map_path")
        if not map_path and isinstance(mp, str) and mp:
            map_path = mp
    if not base and map_path:
        # The workflow's own yield names the viewer; measured 2026-10-03 on a
        # completed map (Congo-Brazzaville) whose render step result was not
        # where this looked, so a finished map was recorded as failed.
        base = Path(map_path).parent.name
    if not base:
        raise RuntimeError(f"no RenderTiledMap output among the steps of {rid}")
    return f"osm-output/maps/tiled/{base}/", edges


def _find(node, wanted: tuple[str, ...]) -> dict:
    out: dict = {}
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k in wanted and k not in out and not isinstance(v, (dict, list)):
                    out[k] = v
                else:
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return out


# --- the router ----------------------------------------------------------------


def router_heap(mb: float) -> str:
    """JVM heap for a region's router: ~6 GB served the 448 MB US major-roads
    graph; small countries need the 1 GB floor."""
    return f"{min(6, max(1, math.ceil(mb / 80) + 1))}g"


def _fleet_router(*args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(FW_ROOT / "fw"), "fleet", "router", "--mongo", mongo_url(), *args],
        capture_output=True,
        text=True,
        cwd=FW_ROOT,
        timeout=timeout,
    )


def region_router_up(key: str, mb: float) -> None:
    """Ask the fleet for a router serving ``key`` and wait until one is ready."""
    r = _fleet_router("add", key, "--heap", router_heap(mb),
                      "--replicas", str(ROUTER_REPLICAS), "--groups", ROUTER_GROUP)
    if r.returncode:
        raise RuntimeError(f"fleet router add {key} failed: {r.stdout[-400:]} {r.stderr[-400:]}")
    r = _fleet_router("wait", key, "--timeout", str(ROUTER_READY_TIMEOUT),
                      timeout=ROUTER_READY_TIMEOUT + 120)
    if r.returncode:
        raise RuntimeError(f"no router for {key} became ready: {r.stdout[-400:]} {r.stderr[-400:]}")


def region_router_down(key: str) -> None:
    r = _fleet_router("remove", key)
    if r.returncode:
        log(f"    WARNING fleet router remove {key} failed: {r.stdout[-200:]} {r.stderr[-200:]}")


def cut_phase(s3c, cont: str, nodes, led) -> bool:
    """Cut every missing sub-region tier under ``cont``. True if anything changed."""
    todo = [
        n
        for n in wp.needing_cut(nodes, cont)
        if led.get(n.key, {}).get("cut_state") not in ("completed", "failed")
    ]
    if not todo:
        return False
    log(
        f"  cut: {len(todo)} region(s) need sub-regions first: "
        + ", ".join(n.key for n in todo[:10])
        + (" ..." if len(todo) > 10 else "")
    )
    running: dict[str, str] = {}
    pending = list(todo)
    while pending or running:
        while pending and len(running) < CUTS_IN_FLIGHT:
            n = pending.pop(0)
            rec = led.setdefault(n.key, {})
            rid = submit(
                PLANET_FFL,
                "osm.planet.BuildAdminSetWorkflow",
                {
                    "source_region": n.key,
                    "admin_level": wp.cut_level(n.key),
                    "bucket": EXTRACTS_BUCKET,
                    "osmfr_fallback": False,
                    "refresh_after_days": 0,
                    "force_refresh": False,
                },
            )
            rec.update(
                cut_state="running",
                cut_runner=rid,
                cut_started=datetime.now(UTC).isoformat(),
            )
            running[n.key] = rid
            ledger_write(led)
            log(f"    cutting {n.key} at admin_level {wp.cut_level(n.key)} ({rid[:8]})")
        time.sleep(60)
        for key, rid in list(running.items()):
            st = run_state(rid)
            if st in ("completed", "failed", "terminated"):
                rec = led[key]
                rec["cut_state"] = "completed" if st == "completed" else "failed"
                if st != "completed":
                    rec["error"] = run_error(rid)
                del running[key]
                ledger_write(led)
                log(
                    f"    cut {key}: {st}"
                    + (f" -- {rec.get('error', '')}" if st != "completed" else "")
                )
            elif (
                time.time() - datetime.fromisoformat(led[key]["cut_started"]).timestamp()
                > CUT_TIMEOUT
            ):
                led[key].update(cut_state="failed", error="cut timed out")
                del running[key]
                ledger_write(led)
    return True


def build_plan(s3c, order, max_mb, led):
    sizes = extract_sizes(s3c, order)
    done = finished_maps(s3c)
    # A cut that finished but left the region with no sub-regions: map it whole
    # rather than not at all, and say why.
    force = {
        k: "cut produced no sub-regions; mapped whole"
        for k, r in led.items()
        if r.get("cut_state") in ("completed", "failed")
        and not any(s.startswith(k + "/") for s in sizes)
    }
    force.update(MAP_WHOLE)
    return wp.plan(sizes, max_mb=max_mb, done=done, force=force, continents=order)


def run_continent(s3c, cont, order, max_mb, led) -> None:
    nodes = build_plan(s3c, order, max_mb, led)
    write_status(s3c, nodes, led, max_mb, order)
    while cut_phase(s3c, cont, nodes, led):
        nodes = build_plan(s3c, order, max_mb, led)
        write_status(s3c, nodes, led, max_mb, order)

    todo = [
        n
        for n in wp.leaves(nodes, cont)
        # The store decides (finished_maps), not the ledger: a map the ledger
        # calls completed may be a first-pass map without the route split.
        if not n.reused and n.key not in _adopting
    ]
    log(
        f"  {cont}: {len(todo)} map(s) to build, est "
        f"{sum(wp.estimate_minutes(n.mb) for n in todo) / 60:.1f} h of map compute"
    )

    need_graph = [n for n in todo if not has_graph(s3c, n.key)]
    for i in range(0, len(need_graph), GRAPH_CHUNK):
        chunk = [n.key for n in need_graph[i : i + GRAPH_CHUNK]]
        log(f"  graphs: building {len(chunk)} ({i + 1}-{i + len(chunk)} of {len(need_graph)})")
        rid = submit(
            LZ_FFL,
            "continental.lz.states.BuildStateGraphs",
            {"states": chunk, "profile": PROFILE, "width": GRAPH_WIDTH},
        )
        st, err = wait(rid, GRAPH_TIMEOUT)
        log(f"  graphs: {st}" + (f" -- {err}" if err else ""))

    # Two passes: a map that failed gets ONE more attempt once the continent's
    # first pass is done. Measured 2026-10-03: three islands failed on one host
    # with "the routing server stopped serving this region mid-run", a handler
    # error marked permanent, so nothing retried them. The ledger records which
    # host ran each attempt, so a repeat on the same host is visible.
    for attempt_pass in (1, 2):
        batch = (
            todo
            if attempt_pass == 1
            else [
                n
                for n in todo
                if led.get(n.key, {}).get("map_state") != "completed"
                and led.get(n.key, {}).get("attempts", 0) < 2
                and has_graph(s3c, n.key)
            ]
        )
        if attempt_pass == 2 and batch:
            log(
                f"  retrying {len(batch)} failed map(s) once: "
                + ", ".join(n.key for n in batch[:8])
            )
        _map_batch(s3c, cont, batch, nodes, led, max_mb, order)


def _zoom_host(rid: str) -> str:
    d = db()
    run = d.runners.find_one({"uuid": rid}, {"workflow_id": 1})
    t = run and d.tasks.find_one(
        {"workflow_id": run["workflow_id"], "name": "osm.Roads.ZoomBuilder.BuildZoomLayers"},
        {"server_id": 1},
    )
    srv = t and d.servers.find_one({"uuid": t.get("server_id")}, {"server_name": 1})
    return (srv or {}).get("server_name", "") if srv else ""


def _map_batch(s3c, cont, todo, nodes, led, max_mb, order) -> None:
    """Build ``todo`` with up to MAPS_IN_FLIGHT maps at once, each on its own
    region router."""
    for n in todo:  # every record exists before any thread touches the ledger
        led.setdefault(n.key, {})
    with ThreadPoolExecutor(max_workers=max(1, MAPS_IN_FLIGHT)) as ex:
        for f in [ex.submit(_build_one, s3c, cont, i, len(todo), n, nodes, led, max_mb, order)
                  for i, n in enumerate(todo, 1)]:
            f.result()


def _build_one(s3c, cont, i, total, n, nodes, led, max_mb, order) -> None:
    with _map_slots:
        _build_one_held(s3c, cont, i, total, n, nodes, led, max_mb, order)


def _build_one_held(s3c, cont, i, total, n, nodes, led, max_mb, order) -> None:
        rec = led.setdefault(n.key, {})
        tag = f"[{cont} {i}/{total}] {n.key} ({n.mb:,.0f} MB)"
        if not has_graph(s3c, n.key):
            rec.update(graph_state="failed", error="no routing graph was built")
            ledger_write(led)
            log(f"{tag}: SKIPPED -- no routing graph")
            return
        log(f"{tag} est {wp.estimate_minutes(n.mb):.0f} min")
        try:
            region_router_up(n.key, n.mb)
            t0 = time.time()
            prior = rec.get("map_runner") if rec.get("map_state") == "running" else None
            if prior and run_state(prior) not in ("completed", "failed", "terminated", "?"):
                # A driver restart while this region's map was building: adopt
                # the live run rather than submit a duplicate beside it.
                rid = prior
                log(f"    adopting the run already in flight ({rid[:8]})")
            else:
                rec["attempts"] = rec.get("attempts", 0) + 1
                rid = submit(
                    LZ_FFL,
                    "continental.lz.states.BuildStateLowZoomMap",
                    {
                        "region": n.key,
                        "slug": wp.slug(n.key),
                        "label": wp.label(n.key),
                        "output_base": OUTPUT_BASE,
                        "memory_gb": wp.estimate_memory_gb(n.mb),
                    },
                )
            rec.update(map_state="running", map_runner=rid)
            ledger_write(led)
            status_write(s3c, nodes, led, max_mb, order)
            st, err = wait(rid, max(3 * 3600, 4 * 60 * wp.estimate_minutes(n.mb)))
            rec.update(map_state=st, error=err, map_minutes=round((time.time() - t0) / 60, 1))
            rec.setdefault("hosts", []).append(_zoom_host(rid))
            if st == "completed":
                src, edges = map_output(rid)
                cities = routed_cities_for(s3c, n.key)
                rec["built"] = datetime.now(UTC).strftime("%Y-%m-%d")
                rec["published"] = pub.publish(
                    s3c,
                    MAPS_BUCKET,
                    n.key,
                    wp.label(n.key),
                    src,
                    edges,
                    cities=cities,
                    built=rec["built"],
                )
                rec["edges"] = edges
                if cities is not None:
                    rec["routed_cities"] = len(cities["features"])
            log(f"{tag}: {st} in {rec['map_minutes']:.0f} min" + (f" -- {err}" if err else ""))
        except Exception as e:  # noqa: BLE001 - one region must not stop the world
            rec.update(map_state="failed", error=f"driver: {e}"[:400])
            log(f"{tag}: DRIVER ERROR: {e}")
        finally:
            region_router_down(n.key)
        ledger_write(led)
        status_write(s3c, nodes, led, max_mb, order)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--continents", default=",".join(wp.CONTINENT_ORDER))
    ap.add_argument("--max-mb", type=float, default=400.0)
    ap.add_argument("--dry", action="store_true", help="print the plan and estimates only")
    a = ap.parse_args()
    order = [c for c in a.continents.split(",") if c]
    s3c = s3()
    led = ledger_read()
    if a.dry:
        nodes = build_plan(s3c, order, a.max_mb, led)
        for cont in order:
            lv = wp.leaves(nodes, cont)
            todo = [n for n in lv if not n.reused]
            cuts = wp.needing_cut(nodes, cont)
            skips = [k for k, n in nodes.items() if n.kind == "skip" and k.startswith(cont + "/")]
            print(
                f"{cont:18} maps {len(lv):4} ({len(lv) - len(todo)} done)  "
                f"est {sum(wp.estimate_minutes(n.mb) for n in todo) / 60:6.1f} h  "
                f"cut first: {len(cuts):3}  skipped: {len(skips)}"
            )
            for n in cuts:
                print(f"    cut {n.key} ({n.mb:,.0f} MB) at admin_level {wp.cut_level(n.key)}")
        return 0
    log(f"world low-zoom run: {', '.join(order)} (max {a.max_mb:,.0f} MB per map)")
    # Routers a killed driver left behind still hold slots: a kill skips the
    # per-map clean-up, so drop every router for a region this driver owns.
    # NOT a map whose run is still in flight: that run is adopted first (below),
    # and pulling its router mid-run would fail it.
    live = {
        k
        for k, r in led.items()
        if r.get("map_state") == "running"
        and r.get("map_runner")
        and run_state(r["map_runner"]) not in ("completed", "failed", "terminated", "?")
    }
    for d in db().fleet_routers.find({}, {"region": 1}):
        if d.get("region") not in led:
            continue
        if d["region"] in live:
            log(f"  keeping the router of a map still in flight: {d['region']}")
            continue
        log(f"  removing a router left from an earlier run: {d['region']}")
        region_router_down(d["region"])
    # Maps still in flight are adopted ALONGSIDE the plan, under the same
    # MAPS_IN_FLIGHT limit (_map_slots). Measured 2026-10-15: kept routers held
    # 5 of 8 slots while the plan started over at Central America, whose routers
    # could then never be placed; and finishing them first instead left 3 of 4
    # slots idle for hours behind one large map.
    adopting = None
    if live:
        nodes = build_plan(s3c, order, a.max_mb, led)
        adopt = [nodes[k] for k in sorted(live) if k in nodes]
        _adopting.update(n.key for n in adopt)
        log(f"  adopting {len(adopt)} map(s) still in flight: " + ", ".join(n.key for n in adopt))
        adopting = threading.Thread(
            target=_map_batch, args=(s3c, "in-flight", adopt, nodes, led, a.max_mb, order)
        )
        adopting.start()
    for cont in order:
        log(f"=== {cont} ===")
        run_continent(s3c, cont, order, a.max_mb, led)
    if adopting is not None:
        adopting.join()
    log("world run finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
