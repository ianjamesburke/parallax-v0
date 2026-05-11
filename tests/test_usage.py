from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor

import pytest

from parallax import runlog, usage


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    yield


def _rec(
    *,
    session_id: str | None = "sess-1",
    backend: str = "anthropic-api",
    alias: str = "mid",
    fal_id: str = "fal-ai/flux/dev",
    tier: str = "mid",
    prompt: str = "a cat",
    output_path: str = "/tmp/x.png",
    duration_ms: int = 100,
    cost_usd: float = 0.025,
    test_mode: bool = False,
    run_id: str | None = None,
) -> usage.UsageRecord:
    return usage.record(
        session_id=session_id,
        backend=backend,
        alias=alias,
        fal_id=fal_id,
        tier=tier,
        prompt=prompt,
        output_path=output_path,
        duration_ms=duration_ms,
        cost_usd=cost_usd,
        test_mode=test_mode,
        run_id=run_id,
    )


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


def test_run_total_from_thread_without_context_propagation():
    """Without context propagation, run_id is None in the worker thread and
    run_total returns 0.0 — this documents the broken behaviour."""
    run_id = runlog.start_run()
    try:
        def _record_in_thread():
            # ContextVar not propagated: current_run_id() → None
            _rec(cost_usd=0.039, run_id=None)

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_record_in_thread).result()

        # Record was written but with run_id=null, so run_total returns 0.0
        assert usage.run_total(run_id) == pytest.approx(0.0)
    finally:
        runlog.end_run()


def test_run_total_from_thread_with_context_propagation():
    """With copy_context().run(), the ContextVar propagates into the thread
    and run_total correctly sums the cost."""
    run_id = runlog.start_run()
    try:
        ctx = contextvars.copy_context()

        def _record_in_thread():
            # run_id resolved via ContextVar — should match the run started above
            _rec(cost_usd=0.039)

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(ctx.run, _record_in_thread).result()

        assert usage.run_total(run_id) == pytest.approx(0.039)
    finally:
        runlog.end_run()
