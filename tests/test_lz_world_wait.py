"""The world driver must not give up on a map that is still building.

Giving up at the estimate made the driver remove the region's router while the
build kept routing; the build fell back to the fleet-wide router, which has no
graph for the region, and failed. Colombia (est 57 min, ~4 h real) failed that
way three times on 2026-10-16/17.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "lz_world.py"


@pytest.fixture
def lz(monkeypatch):
    spec = importlib.util.spec_from_file_location("lz_world_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "time", lambda: clock["t"])
    monkeypatch.setattr(mod.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(mod, "log", lambda *a, **k: None)
    monkeypatch.setattr(mod, "run_error", lambda rid: "boom")
    mod._clock = clock
    return mod


def test_a_running_build_is_waited_on_past_its_estimate(lz, monkeypatch):
    # Running for 5 h against a 1 h budget, then completes.
    monkeypatch.setattr(lz, "run_state",
                        lambda rid: "completed" if lz._clock["t"] >= 5 * 3600 else "running")
    assert lz.wait("r", timeout=3600, hard=24 * 3600) == ("completed", "")


def test_a_build_not_running_still_times_out_at_the_estimate(lz, monkeypatch):
    monkeypatch.setattr(lz, "run_state", lambda rid: "pending")
    st, err = lz.wait("r", timeout=3600, hard=24 * 3600)
    assert st == "timeout" and lz._clock["t"] < 3600 + 60


def test_the_hard_limit_still_applies(lz, monkeypatch):
    monkeypatch.setattr(lz, "run_state", lambda rid: "running")
    st, _ = lz.wait("r", timeout=3600, hard=6 * 3600)
    assert st == "timeout" and 6 * 3600 <= lz._clock["t"] < 6 * 3600 + 60


def test_failure_is_reported_immediately(lz, monkeypatch):
    monkeypatch.setattr(lz, "run_state", lambda rid: "failed")
    assert lz.wait("r", timeout=3600) == ("failed", "boom")
