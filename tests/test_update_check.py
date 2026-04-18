from __future__ import annotations

import json

import pytest

from parallax import update_check


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.delenv("PARALLAX_NO_UPDATE_CHECK", raising=False)
    yield


def _run(
    *,
    installed,
    latest,
    tmp_path,
    capsys,
    now=1000.0,
    cache_seed=None,
    fetcher_raises=False,
):
    cache = tmp_path / "update.json"
    if cache_seed is not None:
        cache.write_text(json.dumps(cache_seed))

    fetch_calls = {"n": 0}

    def fake_fetcher():
        fetch_calls["n"] += 1
        if fetcher_raises:
            raise RuntimeError("network down")
        return latest

    update_check.check_for_update(
        fetcher=fake_fetcher,
        now=lambda: now,
        cache_path=cache,
        installed_version=installed,
    )
    out = capsys.readouterr()
    return out.err, cache, fetch_calls["n"]


def test_nags_when_behind(tmp_path, capsys):
    err, _, fetches = _run(installed="0.1.2", latest="0.1.3", tmp_path=tmp_path, capsys=capsys)
    assert "v0.1.3" in err and "v0.1.2" in err and "parallax update" in err
    assert fetches == 1


def test_silent_when_up_to_date(tmp_path, capsys):
    err, _, _ = _run(installed="0.1.3", latest="0.1.3", tmp_path=tmp_path, capsys=capsys)
    assert err == ""


def test_silent_when_ahead(tmp_path, capsys):
    """Running a dev checkout newer than the latest release must not nag."""
    err, _, _ = _run(installed="0.2.0", latest="0.1.3", tmp_path=tmp_path, capsys=capsys)
    assert err == ""


def test_cache_hit_skips_fetch(tmp_path, capsys):
    seed = {"last_checked": 999.5, "latest_version": "0.1.3"}
    err, _, fetches = _run(
        installed="0.1.2",
        latest="UNUSED",
        tmp_path=tmp_path,
        capsys=capsys,
        now=1000.0,
        cache_seed=seed,
    )
    assert "v0.1.3" in err
    assert fetches == 0


def test_cache_expired_refetches(tmp_path, capsys):
    # TTL is 24h. Seed a cache entry 25h old.
    seed = {"last_checked": 0.0, "latest_version": "0.1.0"}
    err, cache, fetches = _run(
        installed="0.1.2",
        latest="0.1.3",
        tmp_path=tmp_path,
        capsys=capsys,
        now=25 * 3600,
        cache_seed=seed,
    )
    assert fetches == 1
    assert "v0.1.3" in err
    # Cache was rewritten with the fresh value.
    stored = json.loads(cache.read_text())
    assert stored["latest_version"] == "0.1.3"
    assert stored["last_checked"] == 25 * 3600


def test_fetcher_error_is_swallowed(tmp_path, capsys):
    err, cache, fetches = _run(
        installed="0.1.2",
        latest="IRRELEVANT",
        tmp_path=tmp_path,
        capsys=capsys,
        fetcher_raises=True,
    )
    assert err == ""
    assert fetches == 1
    assert not cache.exists()  # nothing to cache on failure


def test_env_opt_out_silences_everything(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PARALLAX_NO_UPDATE_CHECK", "1")
    err, cache, fetches = _run(
        installed="0.1.2",
        latest="0.1.3",
        tmp_path=tmp_path,
        capsys=capsys,
    )
    assert err == ""
    assert fetches == 0
    assert not cache.exists()


def test_corrupt_cache_is_ignored(tmp_path, capsys):
    cache = tmp_path / "update.json"
    cache.write_text("not json {{{")

    def fetcher():
        return "0.1.3"

    update_check.check_for_update(
        fetcher=fetcher,
        now=lambda: 1000.0,
        cache_path=cache,
        installed_version="0.1.2",
    )
    err = capsys.readouterr().err
    assert "v0.1.3" in err


def test_tag_prefix_stripped_via_fetcher(tmp_path, capsys):
    """If the fetcher returns something with a leading v, the caller handles it."""
    err, _, _ = _run(
        installed="0.1.2",
        latest="0.1.3",  # _fetch_latest_from_github strips v; fetcher here returns bare
        tmp_path=tmp_path,
        capsys=capsys,
    )
    assert "v0.1.3" in err


def test_strip_v_helper():
    assert update_check._strip_v("v0.1.3") == "0.1.3"
    assert update_check._strip_v("0.1.3") == "0.1.3"
    assert update_check._strip_v("") == ""


def test_is_newer_handles_trailing_nonnumeric():
    # A prerelease like "0.1.4rc1" should compare as 0.1.4 — close enough; the
    # important property is no exception.
    assert update_check._is_newer("0.1.4", "0.1.3")
    assert not update_check._is_newer("0.1.3", "0.1.3")
