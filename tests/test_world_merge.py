"""Merging region bands into one map."""

from __future__ import annotations

import json

from osm_lz import world_merge as wm


def _line(eid, coords, routed=None):
    p = {"edge_id": eid, "fc": "primary"}
    if routed is not None:
        p["routed"] = routed
    return {
        "type": "Feature",
        "properties": p,
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def test_geom_key_ignores_direction_and_sub_metre_noise():
    a = [[-110.0, 45.0], [-109.9, 45.1]]
    assert wm.geom_key(a) == wm.geom_key(list(reversed(a)))
    assert wm.geom_key(a) == wm.geom_key([[-110.000001, 45.0], [-109.9, 45.1]])
    assert wm.geom_key(a) != wm.geom_key([[-110.0, 45.0], [-109.8, 45.1]])


def test_a_border_road_is_kept_once_and_routed_if_either_side_routed_it(tmp_path):
    border = [[-111.0, 45.0], [-111.0, 45.2]]
    for region, feats in {
        "a": [_line(1, border, routed=False), _line(2, [[-112, 45], [-112, 46]], routed=True)],
        "b": [_line(7, list(reversed(border)), routed=True)],
    }.items():
        (tmp_path / region).mkdir()
        (tmp_path / region / "roads_z4.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": feats})
        )
    out = tmp_path / "band.seq"
    st = wm.merge_band({"a": tmp_path / "a", "b": tmp_path / "b"}, 4, out, {})
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert st["read"] == 3 and st["written"] == 2 and st["border_duplicates"] == 1
    kept = {tuple(map(tuple, r["geometry"]["coordinates"])): r["properties"] for r in rows}
    assert kept[tuple(map(tuple, border))]["routed"] is True  # b's routes count
    assert {r["properties"]["region"] for r in rows} == {"a"}


def test_layers_without_the_attribute_get_it_from_the_band_sbs(tmp_path):
    fc = {"features": [_line(5, [[0, 0], [1, 1]]), _line(6, [[0, 1], [1, 0]])]}
    out = list(wm.band_features(fc, "x", 3, {3: {5: True}}))
    assert [f["properties"]["routed"] for f in out] == [True, False]


def test_retarget_page_renames_the_bands_and_strips_injected_blocks():
    page = (
        "<html><head><title>Montana low-zoom road network</title></head><body>"
        "<span>montana roads z2</span><script>u='roads_z2_tiles_montana_roads_z2-z2_14.pmtiles';"
        "l='montana_roads_z2'</script>\n<!-- fw:cities -->x<!-- /fw:cities -->\n</body>"
        '<style>\n#about-map{}</style><details id="about-map"></details></html>'
    )
    out = wm.retarget_page(page, "montana", "us", "Montana", "United States")
    assert "montana" not in out.lower().replace("united states", "")
    assert "roads_z2_tiles_us_roads_z2-z2_14.pmtiles" in out and "us_roads_z2" in out
    assert "United States roads z2" in out and "fw:cities" not in out and "about-map" not in out
