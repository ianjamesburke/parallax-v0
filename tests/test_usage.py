from __future__ import annotations

import pytest

from parallax import usage


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    yield


def _rec(**over):
    base = dict(
        session_id="sess-1",
        backend="anthropic-api",
        alias="mid",
        fal_id="fal-ai/flux/dev",
        tier="mid",
        prompt="a cat",
        output_path="/tmp/x.png",
        duration_ms=100,
        cost_usd=0.025,
        test_mode=False,
    )
    base.update(over)
    return usage.record(**base)


def test_record_round_trip():
    _rec()
    records = usage.load_records()
    assert len(records) == 1
    assert records[0]["alias"] == "mid"
    assert records[0]["cost_usd"] == pytest.approx(0.025)


def test_test_mode_excluded_by_default():
    _rec(alias="draft", cost_usd=0.0, test_mode=True)
    _rec(alias="mid")
    assert [r["alias"] for r in usage.load_records()] == ["mid"]
    assert {r["alias"] for r in usage.load_records(include_test=True)} == {"draft", "mid"}


def test_summarize_aggregates_by_alias_and_session():
    _rec(alias="draft", cost_usd=0.003, duration_ms=50, session_id="A")
    _rec(alias="draft", cost_usd=0.003, duration_ms=70, session_id="A")
    _rec(alias="premium", cost_usd=0.04, duration_ms=900, session_id="B")

    s = usage.summarize()
    assert s["total_calls"] == 3
    assert s["total_cost_usd"] == pytest.approx(0.046)
    assert s["total_duration_ms"] == 1020
    assert s["session_count"] == 2
    assert s["by_alias"]["draft"]["calls"] == 2
    assert s["by_alias"]["draft"]["cost_usd"] == pytest.approx(0.006)
    assert s["by_alias"]["premium"]["calls"] == 1
    assert s["include_test_mode"] is False


def test_summarize_includes_test_when_requested():
    _rec(alias="draft", cost_usd=0.0, test_mode=True)
    s_real = usage.summarize()
    s_all = usage.summarize(include_test=True)
    assert s_real["total_calls"] == 0
    assert s_all["total_calls"] == 1
    assert s_all["include_test_mode"] is True


def test_usage_path_respects_env_override(tmp_path, monkeypatch):
    p = tmp_path / "elsewhere.ndjson"
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(p))
    assert usage.usage_log_path() == p
