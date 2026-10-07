"""Re-tile an already-built low-zoom map so every edge carries ``routed``.

Maps built before osm_geocoder's zoom export wrote ``routed`` have tiles without
it, so the viewer cannot split a band into routed / name-kind. The pipeline's
own outputs still hold the answer: ``edge_importance.jsonl`` records each edge's
per-zoom SBS, and ``routed`` in band N is SBS_N > 0 -- did THIS band's routes
ride the edge (the same rule the export applies). This rebuilds each band archive from the
run's ``roads_z<N>.geojson`` with that one attribute added, and nothing else
changed: the tippecanoe command is read back out of the archive's own metadata,
so zoom range, layer name and drop strategy are exactly what was published.

It REFUSES a band whose local layer does not hold exactly the published feature
count -- a layer file overwritten by a later run or a replay would otherwise put
different roads under an old map's name.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

from osm_lz.world_publish import pmtiles_metadata, s3_range_reader


def routed_index(jsonl_lines) -> dict[int, dict[int, bool]]:
    """{band zoom: {edge_id: routed}} from edge_importance.jsonl lines.

    Per BAND: an edge revealed at z2 as skeleton is still "routed" in the z5
    band if z5's city-to-city routes rode it."""
    out: dict[int, dict[int, bool]] = {}
    for line in jsonl_lines:
        if not line.strip():
            continue
        e = json.loads(line)
        for z, v in (e.get("sbs") or {}).items():
            out.setdefault(int(z), {})[int(e["edgeId"])] = float(v) > 0.0
    return out


def rebuild_command(generator_options: str, src: str, out: str) -> tuple[list[str], str]:
    """The published tippecanoe command with only its output and input swapped.

    Returns (argv, the original input's basename)."""
    argv = shlex.split(generator_options)
    if not argv or Path(argv[0]).name != "tippecanoe":
        raise ValueError(f"not a tippecanoe command: {generator_options[:120]}")
    if "-o" not in argv:
        raise ValueError("tippecanoe command has no -o")
    i = argv.index("-o")
    argv[i + 1] = out
    original_input = argv[-1]
    if original_input.startswith("-"):
        raise ValueError("tippecanoe command does not end with its input file")
    argv[-1] = src
    return argv, Path(original_input).name


def add_routed(fc: dict, routed: dict[int, bool]) -> int:
    """Set ``routed`` on every feature; return how many are routed."""
    n = 0
    for f in fc["features"]:
        r = routed.get(int(f["properties"]["edge_id"]), False)
        f["properties"]["routed"] = r
        n += r
    return n


def retile_prefix(
    s3, bucket: str, prefix: str, layer_dir: Path, work: Path, tippecanoe: str = "tippecanoe"
) -> list[dict]:
    """Rewrite every band archive the page draws with ``routed`` (recomputed,
    so an older definition is replaced); return a summary per band."""
    layer_dir, work = Path(layer_dir), Path(work)
    work.mkdir(parents=True, exist_ok=True)
    with open(layer_dir / "edge_importance.jsonl", encoding="utf-8") as f:
        routed = routed_index(f)
    keys = [
        o["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
        for o in page.get("Contents", [])
        if o["Key"].endswith(".pmtiles")
    ]
    # Only the archives the page draws. A prefix can hold bands left by an
    # earlier run under other names (the pre-2026-09 tile names carried no
    # region); those are not this map and their layers are long gone.
    try:
        page_html = s3.get_object(Bucket=bucket, Key=prefix + "index.html")["Body"].read().decode()
        keys = [k for k in keys if Path(k).name in page_html]
    except s3.exceptions.NoSuchKey:
        pass
    if not keys:
        raise RuntimeError(f"no band archives under s3://{bucket}/{prefix}")
    # Check every band BEFORE rewriting any, so a mismatch leaves the map whole.
    plan = []
    for key in sorted(keys):
        meta = pmtiles_metadata(s3_range_reader(s3, bucket, key))
        out = work / Path(key).name
        src = work / (Path(key).stem + ".geojson")
        argv, layer_name = rebuild_command(meta["generator_options"], str(src), str(out))
        argv[0] = tippecanoe
        published = sum(
            int(t.get("count", 0)) for t in (meta.get("tilestats") or {}).get("layers", [])
        )
        fc = json.loads((layer_dir / layer_name).read_text())
        if len(fc["features"]) != published:
            raise RuntimeError(
                f"{key}: local {layer_name} has {len(fc['features']):,} features but the "
                f"published tiles hold {published:,} -- the layer file is not the one "
                "these tiles were built from; refusing"
            )
        m = re.match(r"roads_z(\d)\.geojson$", layer_name)
        if not m:
            raise RuntimeError(f"{key}: cannot tell the band zoom from {layer_name}")
        plan.append((key, argv, src, fc, int(m.group(1))))
    summary = []
    for key, argv, src, fc, zoom in plan:
        n = add_routed(fc, routed.get(zoom, {}))
        Path(src).write_text(json.dumps(fc))
        subprocess.run(argv, check=True, capture_output=True, text=True, timeout=3600)
        out = argv[argv.index("-o") + 1]
        ctype = (
            s3.head_object(Bucket=bucket, Key=key).get("ContentType") or "application/octet-stream"
        )
        with open(out, "rb") as fh:
            s3.put_object(Bucket=bucket, Key=key, Body=fh.read(), ContentType=ctype)
        Path(src).unlink()
        Path(out).unlink()
        summary.append({"key": key, "features": len(fc["features"]), "routed": n})
    return summary
