"""Routed-city dots: the replay of the builder's anchor rule, and the page injection."""

from __future__ import annotations

from osm_lz import world_publish as pub

THRESH = {2: 500_000, 3: 200_000, 4: 80_000}
TARGET = {2: 50, 3: 1, 4: 10}


def _city(name, pop, place="city", lon=1.0, lat=2.0):
    return {
        "type": "Feature",
        "properties": {"name": name, "population": pop, "place": place},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


def test_tier_is_the_first_zoom_a_city_anchored_at():
    fc = {
        "features": [
            _city("Big", 900_000),
            _city("Mid", 250_000),
            _city("Mid2", 210_000),
            _city("Small", 90_000),
            _city("Tiny", 500),
        ]
    }
    got = {
        f["properties"]["name"]: f["properties"]["tier"]
        for f in pub.routed_cities(fc, THRESH, TARGET)["features"]
    }
    # z3 takes only ONE (target 1): Big, already tiered at z2 -- so Mid first
    # anchors at z4, as the builder would have it. Tiny is below every threshold.
    assert got == {"Big": 2, "Mid": 4, "Mid2": 4, "Small": 4}


def test_population_strings_and_missing_geometry_are_tolerated():
    fc = {
        "features": [
            _city("S", "300000"),
            {"properties": {"name": "X", "population": 10**6}, "geometry": {}},
        ]
    }
    out = pub.routed_cities(fc, THRESH, TARGET)["features"]
    assert [f["properties"]["name"] for f in out] == ["S"]
    assert out[0]["properties"]["population"] == 300_000


PAGE = "<html><body><div id='title'><small>x</small></div><script>var a=1;</script>\n</body></html>"


def test_injection_is_idempotent_and_carries_the_build_date():
    once = pub.inject_cities(PAGE, "2026-10-07")
    twice = pub.inject_cities(once, "2026-10-08")
    assert once.count("<!-- fw:cities -->") == 1
    assert twice.count("<!-- fw:cities -->") == 1
    assert '"2026-10-08"' in twice and '"2026-10-07"' not in twice
    assert twice.index("<!-- /fw:cities -->") < twice.index("</body>")


def test_names_are_never_written_as_html():
    # OSM names reach the popup through textContent only
    assert "innerHTML" not in pub.CITIES_JS


def test_admin_areas_are_dropped_when_the_builder_says_so():
    fc = {"features": [_city("Seattle", 737_015), _city("Washington", 7_958_180, place="state")]}
    got = [
        f["properties"]["name"]
        for f in pub.routed_cities(fc, THRESH, TARGET, lambda p: p.get("place") != "state")[
            "features"
        ]
    ]
    assert got == ["Seattle"]
