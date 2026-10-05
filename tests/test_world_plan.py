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
