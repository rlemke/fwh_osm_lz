"""The world plan's split rule, against a fake store listing."""

from __future__ import annotations

from osm_lz.world_plan import label, leaves, needing_cut, plan, slug

MB = 1_000_000


def _sizes() -> dict[str, int]:
    s = {
        "central-america/haiti": 70 * MB,
        "central-america/mexico": 753 * MB,  # duplicate of north-america/mexico
        "central-america/nauru-like": 10_000,  # no road network
        "europe/monaco": 1 * MB,
        "europe/france": 5792 * MB,  # too big, no regions yet
        "europe/germany": 5781 * MB,
        "europe/germany/bayern": 993 * MB,  # a too-big state...
        "europe/germany/berlin": 80 * MB,
        "europe/germany/amberg": 5 * MB,  # ...and a district at the SAME depth
        "north-america/us/oregon": 300 * MB,
        "north-america/us/texas": 803 * MB,
        "north-america/us/texas/harris": 90 * MB,
        "north-america/us/florida": 709 * MB,  # too big, but already built
    }
    return s


def test_small_countries_are_one_map_each():
    n = plan(_sizes(), continents=["central-america"])
    assert [x.key for x in leaves(n, "central-america")] == ["central-america/haiti"]
    assert n["central-america/mexico"].kind == "skip"
    assert n["central-america/nauru-like"].kind == "skip"


def test_a_big_country_without_regions_must_be_cut_first():
    n = plan(_sizes(), continents=["europe"])
    assert [x.key for x in needing_cut(n, "europe")] == ["europe/france", "europe/germany/bayern"]
    assert "admin_level 4" in n["europe/france"].reason
    assert "admin_level 6" in n["europe/germany/bayern"].reason


def test_a_mixed_tier_country_uses_only_its_listed_tier():
    n = plan(_sizes(), continents=["europe"])
    assert "europe/germany/amberg" not in n, "a district must not double-cover its state"
    assert [x.key for x in leaves(n, "europe")] == ["europe/germany/berlin", "europe/monaco"]


def test_regions_without_a_country_extract_and_built_maps_are_reused():
    n = plan(_sizes(), continents=["north-america"], done={"north-america/us/florida"})
    assert n["north-america/us"].kind == "split"
    got = [(x.key, x.reused) for x in leaves(n, "north-america")]
    assert got == [
        ("north-america/us/florida", True),
        ("north-america/us/oregon", False),
        ("north-america/us/texas/harris", False),
    ]


def test_labels_and_slugs():
    assert label("europe/bosnia-and-herzegovina") == "Bosnia and Herzegovina"
    assert label("north-america/us") == "United States"
    assert slug("north-america/us/texas/harris") == "north-america-us-texas-harris"


def test_a_region_whose_cut_produced_nothing_is_mapped_whole():
    n = plan(_sizes(), continents=["europe"], force={"europe/france": "cut produced no regions"})
    assert n["europe/france"].kind == "map"
    assert "europe/france" in [x.key for x in leaves(n, "europe")]


def test_memory_floor_tracks_the_measured_build_peaks():
    from osm_lz.world_plan import estimate_memory_gb

    assert estimate_memory_gb(392) == 13  # Washington peaked at 10.6 GB
    assert estimate_memory_gb(5) == 3  # small regions are cheap
    assert 2 <= estimate_memory_gb(0.1) <= 3


def test_memory_floor_takes_the_road_graph_when_it_is_the_bigger_term():
    """Minas Gerais: 279 MB PBF but a 200 MB graph (bigger than Washington's);
    the PBF model gave 11 and it was OOM-killed at 11.8-15.7 GB."""
    from osm_lz.world_plan import estimate_memory_gb

    assert estimate_memory_gb(279) == 11
    assert estimate_memory_gb(279, graph_mb=200.3) >= 14
    # The graph model never LOWERS the floor (Uganda: 96 MB graph, big PBF).
    assert estimate_memory_gb(400, graph_mb=96.5) == estimate_memory_gb(400)
    # Both models agree at the calibration points.
    assert abs(estimate_memory_gb(1449, graph_mb=507.6) - estimate_memory_gb(1449)) <= 2


def test_failed_attempt_escalates_the_floor_up_to_the_cap():
    from osm_lz.world_plan import escalate_memory_gb

    assert escalate_memory_gb(11, 40) == 15
    assert escalate_memory_gb(15, 40) == 21  # MG's 15.7 GB peak is now covered
    assert escalate_memory_gb(4, 40) == 6  # small floors still move
    assert escalate_memory_gb(36, 40) == 40


def test_road_less_regions_are_skipped_with_a_reason():
    """Regions whose every zoom layer (or routing graph) came out empty are left
    out, so a re-run of the world plan does not fail them again."""
    from osm_lz.world_plan import SKIP

    keys = [
        "australia-oceania/tokelau",
        "south-america/south-georgia-and-south-sandwich",
        "europe/norway/jan-mayen",
        "europe/vatican-city",
        "north-america/canada/ontario/pelee-township",
        "north-america/canada/nunavut/sirmilik-national-park",
    ]
    sizes = {k: 2 * MB for k in keys} | {"europe/norway": 900 * MB, "europe/norway/oslo": 50 * MB}
    n = plan(sizes, continents=["europe"])
    assert n["europe/vatican-city"].kind == "skip"
    assert n["europe/norway/jan-mayen"].kind == "skip"
    assert [x.key for x in leaves(n, "europe")] == ["europe/norway/oslo"]
    assert all("road" in SKIP[k] for k in keys)
