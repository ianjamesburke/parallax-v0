from __future__ import annotations

import pytest

from parallax import tools


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("PARALLAX_TEST_MODE", "1")
    monkeypatch.setenv("PARALLAX_OUTPUT_DIR", str(tmp_path / "output"))
    yield


def test_refs_on_unsupported_model_rejected_at_boundary(tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"x")
    with pytest.raises(ValueError, match="does not support reference_images"):
        tools.generate_image(prompt="x", model="draft", reference_images=[str(ref)])


def test_refs_exceeds_max_rejected_at_boundary(tmp_path):
    r1 = tmp_path / "a.png"
    r1.write_bytes(b"x")
    r2 = tmp_path / "b.png"
    r2.write_bytes(b"y")
    with pytest.raises(ValueError, match="at most 1 reference image"):
        tools.generate_image(
            prompt="x", model="mid", reference_images=[str(r1), str(r2)]
        )


def test_refs_missing_file_fails_fast():
    with pytest.raises(ValueError, match="not found"):
        tools.generate_image(
            prompt="x",
            model="nano-banana",
            reference_images=["/nonexistent/thing.png"],
        )


def test_refs_empty_list_is_equivalent_to_none():
    # Empty list should not flip into the edit path or break the shim.
    out = tools.generate_image(prompt="a cat", model="draft", reference_images=[])
    assert out.endswith(".png")


def test_tool_schema_exposes_reference_images_field():
    schema = tools.TOOL_SCHEMAS[0]["input_schema"]
    props = schema["properties"]
    assert "reference_images" in props
    assert props["reference_images"]["type"] == "array"
    assert props["reference_images"]["items"] == {"type": "string"}
    # Still optional — only prompt + model required
    assert set(schema["required"]) == {"prompt", "model"}
