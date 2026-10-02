#!/usr/bin/env python
"""Build the low-zoom road map for the world, continent by continent.

Plan (``osm_lz.world_plan``): one map per country at or under ``--max-mb``; a
bigger country is broken into its sub-regions, recursively, and sub-regions the
object store does not have yet are CUT first (``osm.planet.BuildAdminSetWorkflow``).

Per continent, in order:

1. **cut** the sub-regions a too-big region needs (a few at a time, on the fleet);
2. **graphs** -- build every leaf's routing graph (parallel across the fleet);
3. **maps** -- ONE region at a time, because graphhopper-web serves exactly one
   region: repoint the ``graphhopper`` role, wait until EVERY router host is
   serving the new region, run ``BuildStateLowZoomMap``, publish the viewer into
   the gallery's world tree (``cache/osm/maps/lz/<key>/``).

Everything is a submitted WORKFLOW; this only sequences them, which no FFL can
express because the router is fleet configuration, not a step.

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
import os
import subprocess
import sys
import time
import urllib.request
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
ROUTER_PORT = int(os.environ.get("FW_GRAPHHOPPER_PORT", "8989"))
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


def router_hosts() -> list[tuple[str, str]]:
    """(name, address) of every host that runs the graphhopper role."""
    cat = _catalog()
    out = []
    for srv in cat.servers():
        if srv.get("group") == ROUTER_GROUP:
            ip = cat.resolve_ip(srv)
            if ip:
                out.append((cat.host_key(srv.get("name")), ip))
    return out


def db():
    from pymongo import MongoClient

    return MongoClient(mongo_url(), serverSelectionTimeoutMS=15000).facetwork


# --- ledger + status ---------------------------------------------------------


def ledger_read() -> dict:
    try:
        return json.loads(LEDGER.read_text())
    except (OSError, ValueError):
        return {}


def ledger_write(d: dict) -> None:
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


def finished_maps(s3c) -> set[str]:
    done = set()
    for page in s3c.get_paginator("list_objects_v2").paginate(
        Bucket=MAPS_BUCKET, Prefix=pub.LZ_PREFIX
    ):
        for o in page.get("Contents", []):
            k = o["Key"]
            if k.endswith("/index.html"):
                done.add(k[len(pub.LZ_PREFIX) : -len("/index.html")])
    return done


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
    edges, base = None, ""
    for st in d.steps.find({"workflow_id": run["workflow_id"]}, {"facet_name": 1, "attributes": 1}):
        found = _find(st.get("attributes") or {}, ("output_path", "selected_edges"))
        if edges is None and found.get("selected_edges") is not None:
            try:
                edges = int(found["selected_edges"])
            except (TypeError, ValueError):
                pass
        op = found.get("output_path")
        if st.get("facet_name") == "osm.viz.RenderTiledMap" and isinstance(op, str) and op:
            base = Path(op).parent.name
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


def _info(ip: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://{ip}:{ROUTER_PORT}/info", timeout=8) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001 - "not serving" is the answer
        return None


def current_router_region() -> str:
    r = subprocess.run(
        [str(FW_ROOT / "fw"), "fleet", "get", "--mongo", mongo_url()],
        capture_output=True,
        text=True,
        cwd=FW_ROOT,
    )
    for line in r.stdout.splitlines():
        if "role graphhopper:" in line and "region=" in line:
            return line.split("region=", 1)[1].split()[0]
    return ""


#: How long a router host may stay entirely SILENT before it is reported as
#: unverifiable from here rather than waited for. Measured 2026-10-02: one host
#: (a laptop on Docker Desktop) served the region correctly to its own runners
#: but returned "empty reply" to LAN probes, so every switch would have sat out
#: the full timeout. Silence is not staleness: a host that ANSWERS with the old
#: region is still waited for, because that is the failure this guards against.
SILENT_GRACE_S = 5 * 60
#: A host that was silent on the previous switch gets only this long on the
#: next one: at ~600 maps, a full grace per switch would add ~50 h of waiting
#: for a host that has never once answered. It is re-probed every switch, so a
#: host that starts answering is waited for again.
KNOWN_SILENT_GRACE_S = 45
_known_silent: set[str] = set()


def point_router_at(key: str) -> None:
    """Repoint the graphhopper role and wait until every router host serves ``key``.

    "Serving" is measured, not assumed: each host's /info must answer with a
    bbox different from what it served before the switch. A healthy server still
    holding the previous region is exactly the failure this sequence exists to
    prevent (it 400s every pair, and the map comes out hollow).
    """
    hosts = router_hosts()
    if current_router_region() == key:
        up = [n for n, ip in hosts if (_info(ip) or {}).get("bbox")]
        if up:
            log(f"    router already serving {key} ({len(up)} host(s) answering)")
            return
    before = {name: (_info(ip) or {}).get("bbox") for name, ip in hosts}
    r = subprocess.run(
        [str(FW_ROOT / "fw"), "fleet", "set", "--mongo", mongo_url(), "--graphhopper-region", key],
        capture_output=True,
        text=True,
        cwd=FW_ROOT,
    )
    if r.returncode != 0:
        raise RuntimeError(f"fleet set failed: {r.stdout[-600:]} {r.stderr[-600:]}")
    t0 = time.time()
    deadline = t0 + ROUTER_READY_TIMEOUT
    ready: set[str] = set()
    answered: set[str] = set()
    while time.time() < deadline:
        for name, ip in hosts:
            if name in ready:
                continue
            info = _info(ip)
            if info:
                answered.add(name)
            if info and info.get("bbox") and info["bbox"] != before[name]:
                ready.add(name)
        if len(ready) == len(hosts):
            _known_silent.clear()
            log(f"    router serving {key} on {len(ready)} host(s)")
            return
        silent = [n for n, _ in hosts if n not in ready and n not in answered]
        grace = KNOWN_SILENT_GRACE_S if silent and set(silent) <= _known_silent else SILENT_GRACE_S
        if ready and time.time() - t0 > grace and len(ready) + len(silent) == len(hosts):
            _known_silent.clear()
            _known_silent.update(silent)
            log(
                f"    router serving {key} on {len(ready)} host(s); not verifiable from "
                f"here (no answer at all): {', '.join(silent)}"
            )
            return
        time.sleep(15)
    if ready:
        log(f"    WARNING router serving {key} on {len(ready)} of {len(hosts)} host(s) only")
        return
    raise RuntimeError(
        f"no router host came up serving {key} within {ROUTER_READY_TIMEOUT // 60} min"
    )


# --- phases ----------------------------------------------------------------------


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
        if not n.reused and led.get(n.key, {}).get("map_state") != "completed"
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

    for i, n in enumerate(todo, 1):
        rec = led.setdefault(n.key, {})
        tag = f"[{cont} {i}/{len(todo)}] {n.key} ({n.mb:,.0f} MB)"
        if not has_graph(s3c, n.key):
            rec.update(graph_state="failed", error="no routing graph was built")
            ledger_write(led)
            log(f"{tag}: SKIPPED -- no routing graph")
            continue
        log(f"{tag} est {wp.estimate_minutes(n.mb):.0f} min")
        try:
            point_router_at(n.key)
            t0 = time.time()
            rid = submit(
                LZ_FFL,
                "continental.lz.states.BuildStateLowZoomMap",
                {
                    "region": n.key,
                    "slug": wp.slug(n.key),
                    "label": wp.label(n.key),
                    "output_base": OUTPUT_BASE,
                },
            )
            rec.update(map_state="running", map_runner=rid)
            ledger_write(led)
            write_status(s3c, nodes, led, max_mb, order)
            st, err = wait(rid, max(3 * 3600, 4 * 60 * wp.estimate_minutes(n.mb)))
            rec.update(map_state=st, error=err, map_minutes=round((time.time() - t0) / 60, 1))
            if st == "completed":
                src, edges = map_output(rid)
                rec["published"] = pub.publish(s3c, MAPS_BUCKET, n.key, wp.label(n.key), src, edges)
                rec["edges"] = edges
            log(f"  {st} in {rec['map_minutes']:.0f} min" + (f" -- {err}" if err else ""))
        except Exception as e:  # noqa: BLE001 - one region must not stop the world
            rec.update(map_state="failed", error=f"driver: {e}"[:400])
            log(f"  DRIVER ERROR: {e}")
        ledger_write(led)
        write_status(s3c, nodes, led, max_mb, order)


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
    for cont in order:
        log(f"=== {cont} ===")
        run_continent(s3c, cont, order, a.max_mb, led)
    log("world run finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
