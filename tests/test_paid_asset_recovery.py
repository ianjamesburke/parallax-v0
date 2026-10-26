from __future__ import annotations

import yaml

from parallax import cli


def test_produce_failure_after_asset_prints_recovery_and_log_pointer(monkeypatch, tmp_path, capsys):
    folder = tmp_path / "project"
    folder.mkdir()
    plan = folder / "plan.yaml"
    plan.write_text(yaml.safe_dump({"scenes": [{"index": 0, "prompt": "still"}]}))
    assets = folder / "parallax" / "assets"
    assets.mkdir(parents=True)
    (assets / "scene_00_still.png").write_bytes(b"paid-looking asset")

    class LateFailure(RuntimeError):
        pass

    failure = LateFailure("assembly exploded")
    failure.parallax_run_id = "run-after-spend"
    import parallax.produce
    monkeypatch.setattr(parallax.produce, "run_plan", lambda **kwargs: (_ for _ in ()).throw(failure))

    assert cli.main(["produce", "--folder", str(folder), "--plan", str(plan)]) == 1
    err = capsys.readouterr().err
    assert "Paid assets are saved at" in err
    assert "do not regenerate them" in err
    assert "LateFailure: assembly exploded" in err
    assert "parallax log run-after-spend" in err
