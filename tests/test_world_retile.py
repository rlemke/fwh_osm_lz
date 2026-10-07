"""Re-tiling a published map with `routed`, and the viewer's band split."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from osm_lz import world_publish as pub
from osm_lz import world_retile as rt

OPTS = (
    "tippecanoe -o /tmp/tmpx.mbtiles -Z 7 -z 14 --force --layer wa_roads_z7 "
    "--drop-densest-as-needed --coalesce-densest-as-needed --read-parallel "
    "/scratch/output/lz-states/washington/roads_z7.geojson"
)


def test_the_published_command_is_reused_with_only_output_and_input_swapped():
    argv, name = rt.rebuild_command(OPTS, "/w/in.geojson", "/w/out.pmtiles")
    assert name == "roads_z7.geojson"
    assert argv[argv.index("-o") + 1] == "/w/out.pmtiles"
    assert argv[-1] == "/w/in.geojson"
    assert argv[argv.index("--layer") + 1] == "wa_roads_z7"
    assert "--drop-densest-as-needed" in argv and argv[argv.index("-Z") + 1] == "7"


def test_a_non_tippecanoe_command_is_refused():
    with pytest.raises(ValueError):
        rt.rebuild_command("ogr2ogr -f MVT out in.geojson", "a", "b")


def test_routed_is_per_band_not_per_reveal_zoom():
    lines = [
        # an Interstate: revealed at z2 as skeleton, where nothing routes, but
        # ridden by every z5 route -- it is "routed" in the z5 band
        json.dumps({"edgeId": 1, "minZoom": 2, "sbs": {"2": 0.0, "5": 0.8}}),
        json.dumps({"edgeId": 2, "minZoom": 5, "sbs": {"2": 0.0, "5": 0.0}}),
        "",
    ]
    idx = rt.routed_index(lines)
    assert idx[5] == {1: True, 2: False}
    assert idx[2] == {1: False, 2: False}


def test_add_routed_defaults_unknown_edges_to_name_kind():
    fc = {"features": [{"properties": {"edge_id": 1}}, {"properties": {"edge_id": 9}}]}
    assert rt.add_routed(fc, {1: True}) == 1
    assert [f["properties"]["routed"] for f in fc["features"]] == [True, False]


PAGE = (
    "<html><body><div id='legend'><label class='row'><input type='checkbox' class='lyr' "
    "data-layer='layer0' checked></label></div><script>var LAYER_IDS={};</script>\n</body></html>"
)


def test_split_injection_is_idempotent_and_sits_after_the_city_layer():
    html = pub.inject_cities(PAGE, "2026-10-07")
    html = pub.inject_route_split(pub.inject_route_split(html))
    assert html.count("<!-- fw:route-split -->") == 1
    assert html.index("<!-- /fw:cities -->") < html.index("<!-- fw:route-split -->")
    assert html.index("<!-- /fw:route-split -->") < html.index("</body>")


def test_both_halves_ticked_means_no_filter():
    # the band must draw exactly as before when both boxes are ticked
    assert "if(s.r&&s.o)return null" in pub.SPLIT_JS


@pytest.mark.skipif(shutil.which("tippecanoe") is None, reason="needs tippecanoe")
def test_metadata_reader_sees_the_attribute_tippecanoe_wrote(tmp_path):
    src = tmp_path / "x.geojson"
    src.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"edge_id": 1, "routed": True},
                        "geometry": {"type": "LineString", "coordinates": [[0, 0], [0.1, 0.1]]},
                    }
                ],
            }
        )
    )
    out = tmp_path / "x.pmtiles"
    subprocess.run(
        ["tippecanoe", "-q", "-o", str(out), "-Z", "2", "-z", "6", "--layer", "l", str(src)],
        check=True,
    )
    data = out.read_bytes()
    meta = pub.pmtiles_metadata(lambda off, n: data[off : off + n])
    assert pub.tiles_fields(meta) == {"edge_id", "routed"}
    assert "tippecanoe" in meta["generator_options"]
