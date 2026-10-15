"""Reference adaptation preserves the whole sheet and never edits source files."""

import base64
from io import BytesIO

import httpx
import pytest
from PIL import Image

from parallax.models import IMAGE_MODELS
from parallax.openrouter import image


@pytest.fixture
def requests(monkeypatch):
    bodies = []
    monkeypatch.setattr(image, "_check_key", lambda: None)
    monkeypatch.setattr(image, "describe_reference", lambda path: "")
    monkeypatch.setattr(image, "note_billed_cost", lambda data: None)
    monkeypatch.setattr(image, "note_generation_id", lambda value: None)

    def post(endpoint, body, **kwargs):
        assert endpoint == "/images"
        bodies.append(body)
        # Same /images response envelope as test_openrouter_request_shapes.py.
        return httpx.Response(
            200,
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/images"),
            json={"data": [{"b64_json": base64.b64encode(b"output").decode(),
                             "media_type": "image/png"}]},
        )

    monkeypatch.setattr(image, "_post", post)
    return bodies


def reference_image(body):
    url = body["input_references"][0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    return Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1])))


@pytest.mark.parametrize("aspect,size", [("9:16", (720, 1280)), ("1:1", (1024, 1024))])
def test_landscape_sheet_edges_survive_portrait_and_square_requests(tmp_path, requests, aspect, size):
    source = tmp_path / "characters.png"
    sheet = Image.new("RGB", (600, 200), "green")
    sheet.paste("red", (0, 0, 100, 200))
    sheet.paste("blue", (500, 0, 600, 200))
    sheet.save(source)
    original = source.read_bytes()
    for attempt in range(2):
        image._image_real(
            "square character sheet", IMAGE_MODELS["draft"], [source], tmp_path,
            aspect_ratio=aspect, out_file=tmp_path / f"result-{attempt}.png",
        )
        assert source.read_bytes() == original

    assert requests[0]["input_references"] == requests[1]["input_references"]
    encoded = reference_image(requests[0])
    assert encoded.size == size
    assert encoded.getpixel((5, size[1] // 2)) == (255, 0, 0)
    assert encoded.getpixel((size[0] - 6, size[1] // 2)) == (0, 0, 255)
    assert encoded.getpixel((size[0] // 2, 0)) == (255, 255, 255)
    assert set(path.name for path in tmp_path.iterdir()) == {
        "characters.png", "result-0.png", "result-1.png"
    }


def test_transparency_composites_over_neutral_canvas(tmp_path, requests):
    source = tmp_path / "transparent.png"
    sheet = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    sheet.paste((255, 0, 0, 128), (200, 200, 800, 800))
    sheet.save(source)
    original = source.read_bytes()
    image._image_real("x", IMAGE_MODELS["draft"], [source], tmp_path, aspect_ratio="1:1")
    encoded = reference_image(requests[0])
    assert encoded.mode == "RGB"
    assert encoded.getpixel((0, 0)) == (255, 255, 255)
    assert encoded.getpixel((500, 500)) == (255, 127, 127)
    assert source.read_bytes() == original


def test_no_aspect_keeps_original_reference_payload(tmp_path, requests):
    source = tmp_path / "sheet.png"
    Image.new("RGB", (600, 200), "red").save(source)
    original = source.read_bytes()
    image._image_real("x", IMAGE_MODELS["draft"], [source], tmp_path)
    url = requests[0]["input_references"][0]["image_url"]["url"]
    assert base64.b64decode(url.split(",", 1)[1]) == original
    assert source.read_bytes() == original
