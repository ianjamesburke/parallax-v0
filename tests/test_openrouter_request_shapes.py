"""Real-mode HTTP request-shape tests.

Each test mocks `httpx.post` / `httpx.get` and asserts the wire shape of
the request body. This locks in every gotcha discovered during live API
verification — if the schema drifts, these fail loudly with a diff
instead of producing a runtime ZodError 400 in front of the user.

Gotchas locked in here (verified live 2026-04-28):
  - `frame_images` must be an array of objects with `type='image_url'`,
    `frame_type` ∈ {'first_frame', 'last_frame'}, and `image_url.url`.
  - The pricing.py `model_id` field is namespaced as `openrouter/<vendor>/<model>`;
    the wire `model` parameter strips the leading `openrouter/`.
  - `_image_real` passes `size` only when explicitly provided.
  - `_video_real` passes `size` and `aspect_ratio` only when explicitly
    provided.
  - Submit response must contain `polling_url`; missing it raises with
    a clear message.
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest

from parallax import openrouter, runlog


@pytest.fixture(autouse=True)
def _real_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("PARALLAX_TEST_MODE", raising=False)
    monkeypatch.setenv("PARALLAX_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("PARALLAX_USAGE_LOG", str(tmp_path / "usage.ndjson"))
    runlog.start_run("shape-test")
    yield
    runlog.end_run()


# ---------------------------------------------------------------------------
# Tiny httpx fakes
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code: int = 200, json_body: dict | None = None, content: bytes = b""):
        self.status_code = status_code
        self._json = json_body or {}
        self.content = content
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore[arg-type]

    def json(self):
        return self._json


class _Recorder:
    def __init__(self):
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []


# ---------------------------------------------------------------------------
# Image
# ---------------------------------------------------------------------------

def test_image_real_strips_openrouter_prefix_from_model_id(monkeypatch, tmp_path):
    rec = _Recorder()

    def fake_post(url, *, headers, json, timeout):
        rec.posts.append((url, json))
        # Minimal valid image response shape
        b64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
        return _FakeResponse(200, {
            "choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{b64}"}}]}}],
        })

    monkeypatch.setattr(openrouter.httpx if hasattr(openrouter, "httpx") else httpx, "post", fake_post, raising=False)
    monkeypatch.setattr(httpx, "post", fake_post)

    out = openrouter.generate_image("a red apple", alias="draft", out_dir=tmp_path)
    assert out.exists()
    body = rec.posts[0][1]
    assert body["model"] == "google/gemini-2.5-flash-image"  # NO leading 'openrouter/'
    assert body["modalities"] == ["image", "text"]
    assert "size" not in body  # not passed when not requested


def test_image_real_passes_size_when_specified(monkeypatch, tmp_path):
    rec = _Recorder()
    b64 = base64.b64encode(b"\x89PNG").decode()

    def fake_post(url, *, headers, json, timeout):
        rec.posts.append((url, json))
        return _FakeResponse(200, {
            "choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{b64}"}}]}}],
        })
    monkeypatch.setattr(httpx, "post", fake_post)

    openrouter.generate_image("x", alias="draft", out_dir=tmp_path, size="1080x720")
    assert rec.posts[0][1]["size"] == "1080x720"


def test_image_real_encodes_reference_images_as_data_url_parts(monkeypatch, tmp_path):
    rec = _Recorder()
    b64 = base64.b64encode(b"\x89PNG").decode()
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\nfake-ref")

    def fake_post(url, *, headers, json, timeout):
        rec.posts.append((url, json))
        return _FakeResponse(200, {
            "choices": [{"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{b64}"}}]}}],
        })
    monkeypatch.setattr(httpx, "post", fake_post)

    openrouter.generate_image("x", alias="nano-banana", reference_images=[ref], out_dir=tmp_path)
    content = rec.posts[0][1]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_image_real_raises_when_response_has_no_images(monkeypatch, tmp_path):
    def fake_post(url, *, headers, json, timeout):
        return _FakeResponse(200, {"choices": [{"message": {}}]})
    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(RuntimeError, match="all fallbacks exhausted"):
        openrouter.generate_image("x", alias="draft", out_dir=tmp_path)


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------

def test_video_real_submits_correct_frame_images_array_shape(monkeypatch, tmp_path):
    """frame_images MUST be an array of {type='image_url', frame_type, image_url.url}.
    Object shape, or 'position' instead of 'frame_type', returns ZodError 400."""
    rec = _Recorder()
    img = tmp_path / "first.png"
    img.write_bytes(b"\x89PNG")

    def fake_post(url, *, headers, json, timeout):
        rec.posts.append((url, json))
        return _FakeResponse(202, {"id": "abc", "polling_url": "https://example.com/abc", "status": "pending"})

    def fake_get(url, *, headers, timeout):
        rec.gets.append(url)
        return _FakeResponse(200, {
            "id": "abc", "status": "completed",
            "unsigned_urls": ["https://example.com/abc/content"],
        })

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(200, {
        "id": "abc", "status": "completed", "unsigned_urls": ["https://example.com/abc/content"],
    }) if "/abc" in (a[0] if a else kw.get("url", "")) else _FakeResponse(200, {}, content=b"FAKE_MP4_BYTES"))
    # Need a separate mock for the download GET. Use a stateful one:
    state = {"calls": 0}
    def stateful_get(url, *, headers, timeout):
        state["calls"] += 1
        rec.gets.append(url)
        if state["calls"] == 1:
            return _FakeResponse(200, {
                "id": "abc", "status": "completed",
                "unsigned_urls": ["https://example.com/abc/content"],
            })
        return _FakeResponse(200, content=b"FAKE_MP4_BYTES")
    monkeypatch.setattr(httpx, "get", stateful_get)
    # Speed up the poll loop
    monkeypatch.setattr(openrouter, "_VIDEO_POLL_INTERVAL_S", 0.0)

    openrouter.generate_video("test", alias="seedance", image_path=img, duration_s=4.0, out_dir=tmp_path)

    body = rec.posts[0][1]
    assert isinstance(body["frame_images"], list), "frame_images must be array, not object"
    elem = body["frame_images"][0]
    assert elem["type"] == "image_url"
    assert elem["frame_type"] == "first_frame"  # NOT 'position'
    assert elem["image_url"]["url"].startswith("data:image/png;base64,")


def test_video_real_passes_size_and_aspect_ratio_only_when_specified(monkeypatch, tmp_path):
    rec = _Recorder()

    def fake_post(url, *, headers, json, timeout):
        rec.posts.append((url, json))
        return _FakeResponse(202, {"id": "x", "polling_url": "https://example.com/x", "status": "pending"})

    state = {"calls": 0}
    def stateful_get(url, *, headers, timeout):
        state["calls"] += 1
        if state["calls"] == 1:
            return _FakeResponse(200, {
                "status": "completed", "unsigned_urls": ["https://example.com/x/content"],
            })
        return _FakeResponse(200, content=b"BYTES")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", stateful_get)
    monkeypatch.setattr(openrouter, "_VIDEO_POLL_INTERVAL_S", 0.0)

    # Without size/aspect_ratio
    openrouter.generate_video("x", alias="seedance", duration_s=4, out_dir=tmp_path)
    body = rec.posts[-1][1]
    assert "size" not in body and "aspect_ratio" not in body

    # With both
    state["calls"] = 0
    openrouter.generate_video("x", alias="seedance", duration_s=4, out_dir=tmp_path,
                               size="1280x720", aspect_ratio="16:9")
    body = rec.posts[-1][1]
    assert body["size"] == "1280x720"
    assert body["aspect_ratio"] == "16:9"


def test_video_real_strips_openrouter_prefix_from_model_id(monkeypatch, tmp_path):
    rec = _Recorder()

    def fake_post(url, *, headers, json, timeout):
        rec.posts.append((url, json))
        return _FakeResponse(202, {"id": "x", "polling_url": "https://example.com/x", "status": "pending"})

    state = {"calls": 0}
    def stateful_get(url, *, headers, timeout):
        state["calls"] += 1
        if state["calls"] == 1:
            return _FakeResponse(200, {
                "status": "completed", "unsigned_urls": ["https://example.com/x/content"],
            })
        return _FakeResponse(200, content=b"BYTES")

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", stateful_get)
    monkeypatch.setattr(openrouter, "_VIDEO_POLL_INTERVAL_S", 0.0)

    openrouter.generate_video("x", alias="seedance", duration_s=4, out_dir=tmp_path)
    body = rec.posts[0][1]
    assert body["model"] == "bytedance/seedance-2.0-fast"  # NO leading 'openrouter/'


def test_video_real_raises_when_submit_returns_no_polling_url(monkeypatch, tmp_path):
    def fake_post(url, *, headers, json, timeout):
        return _FakeResponse(202, {"id": "x", "status": "pending"})  # missing polling_url
    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(RuntimeError, match="all fallbacks exhausted"):
        openrouter.generate_video("x", alias="seedance", duration_s=4, out_dir=tmp_path)


def test_video_real_raises_on_failed_status(monkeypatch, tmp_path):
    def fake_post(url, *, headers, json, timeout):
        return _FakeResponse(202, {"id": "x", "polling_url": "https://example.com/x", "status": "pending"})
    def fake_get(url, *, headers, timeout):
        return _FakeResponse(200, {"status": "failed", "error": "model returned no output"})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(openrouter, "_VIDEO_POLL_INTERVAL_S", 0.0)

    with pytest.raises(RuntimeError, match="all fallbacks exhausted"):
        openrouter.generate_video("x", alias="seedance", duration_s=4, out_dir=tmp_path)


# ---------------------------------------------------------------------------
# Pricing model_id correctness — these aliases must point to slugs that
# /api/v1/videos/models actually serves.
# ---------------------------------------------------------------------------

def test_video_pricing_model_ids_match_live_openrouter_slugs():
    """Locks in the corrected slugs from 2026-04-28. If OpenRouter ever
    drops or renames these, this fails loud and the user knows to check
    `/api/v1/videos/models`."""
    from parallax.models import VIDEO_MODELS
    expected = {
        "kling": "openrouter/kwaivgi/kling-video-o1",
        "veo": "openrouter/google/veo-3.1",
        "seedance": "openrouter/bytedance/seedance-2.0-fast",
        "wan": "openrouter/alibaba/wan-2.7",
        "sora": "openrouter/openai/sora-2-pro",
    }
    for alias, expected_id in expected.items():
        assert alias in VIDEO_MODELS, f"missing alias {alias} in VIDEO_MODELS"
        assert VIDEO_MODELS[alias].model_id == expected_id, (
            f"alias {alias!r} drifted: have {VIDEO_MODELS[alias].model_id!r}, "
            f"expected {expected_id!r} (verified hosted on OpenRouter 2026-04-28)"
        )


# ---------------------------------------------------------------------------
# Gemini TTS — /api/v1/audio/speech request shape
# ---------------------------------------------------------------------------

def _make_minimal_pcm() -> bytes:
    # Raw 16-bit mono PCM at 24kHz (1 second of silence)
    # OpenRouter /audio/speech returns raw PCM when response_format="pcm"
    return b"\x00\x00" * 24000


def test_gemini_tts_posts_to_audio_speech_endpoint(monkeypatch, tmp_path):
    """tts-gemini must POST to /audio/speech, not /chat/completions."""
    rec = _Recorder()

    def fake_post(url, *, headers, json, timeout):
        rec.posts.append((url, json))
        return _FakeResponse(200, content=_make_minimal_pcm())

    monkeypatch.setattr(httpx, "post", fake_post)

    openrouter.generate_tts("Hello world", alias="tts-gemini", voice="Kore", out_dir=tmp_path)

    assert len(rec.posts) == 1
    url, body = rec.posts[0]
    assert url.endswith("/audio/speech"), (
        f"tts-gemini must use /audio/speech endpoint, got: {url}"
    )


def test_gemini_tts_request_body_shape(monkeypatch, tmp_path):
    """Wire shape: model (no 'openrouter/' prefix), input, voice, response_format."""
    rec = _Recorder()

    def fake_post(url, *, headers, json, timeout):
        rec.posts.append((url, json))
        return _FakeResponse(200, content=_make_minimal_pcm())

    monkeypatch.setattr(httpx, "post", fake_post)

    openrouter.generate_tts(
        "[dramatically] Hello world", alias="tts-gemini", voice="Fenrir", out_dir=tmp_path,
    )

    _, body = rec.posts[0]
    assert body["model"] == "google/gemini-3.1-flash-tts-preview"  # no 'openrouter/' prefix
    assert body["voice"] == "Fenrir"
    # "wav" returns ZodError 400 — only "mp3" or "pcm" accepted (verified live 2026-04-30)
    assert body["response_format"] == "pcm"
    assert "input" in body


def test_gemini_tts_passes_emotional_tags_in_input(monkeypatch, tmp_path):
    """Emotional tags must appear in the `input` field sent to the API — not stripped."""
    rec = _Recorder()

    def fake_post(url, *, headers, json, timeout):
        rec.posts.append((url, json))
        return _FakeResponse(200, content=_make_minimal_pcm())

    monkeypatch.setattr(httpx, "post", fake_post)

    text = "[dramatically] The world ends. [softly] Just like that."
    openrouter.generate_tts(text, alias="tts-gemini", voice="Kore", out_dir=tmp_path)

    _, body = rec.posts[0]
    assert "[dramatically]" in body["input"], (
        "Gemini TTS must receive emotional tags in input unchanged"
    )
    assert "[softly]" in body["input"]


def test_openai_tts_strips_tags_from_chat_completions_body(monkeypatch, tmp_path):
    """tts-mini must strip [emotional] tags before the text reaches the API."""
    import json as _json

    posted_bodies: list[dict] = []
    pcm_chunk = _json.dumps({
        "choices": [{
            "delta": {
                "audio": {
                    "data": __import__("base64").b64encode(b"\x00\x00" * 100).decode(),
                    "transcript": "hello",
                }
            }
        }]
    })

    class _FakeStreamResponse:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_lines(self):
            yield f"data: {pcm_chunk}"
            yield "data: [DONE]"

    import contextlib

    @contextlib.contextmanager
    def fake_stream(method, url, *, headers, json, timeout):
        posted_bodies.append(json)
        yield _FakeStreamResponse()

    monkeypatch.setattr(httpx, "stream", fake_stream)

    text = "[dramatically] Everything changed. [softly] No one knew."
    openrouter.generate_tts(text, alias="tts-mini", voice="nova", out_dir=tmp_path)

    assert posted_bodies, "expected at least one stream POST"
    user_content = posted_bodies[0]["messages"][0]["content"]
    assert "[dramatically]" not in user_content, (
        "tts-mini must strip emotional tags before sending to chat/completions"
    )
    assert "Everything changed." in user_content


def test_tts_gemini_model_id_matches_openrouter_slug():
    """Lock in the Gemini TTS model ID. Verified hosted on OpenRouter 2026-04-30."""
    from parallax.models import TTS_MODELS
    assert "tts-gemini" in TTS_MODELS
    assert TTS_MODELS["tts-gemini"].model_id == "openrouter/google/gemini-3.1-flash-tts-preview"
    assert TTS_MODELS["tts-gemini"].tts_backend == "speech"
