from __future__ import annotations

from PIL import Image

from parallax import cli


def test_crop_grid_writes_selected_cell_and_all_cells(tmp_path, capsys):
    source = tmp_path / "options.png"
    image = Image.new("RGB", (4, 2))
    for y in range(2):
        for x in range(4):
            image.putpixel((x, y), (x + y * 4, 0, 0))
    image.save(source)
    selected = tmp_path / "chosen" / "panel.png"
    all_dir = tmp_path / "all"
    assert cli.main(["image", "crop-grid", str(source), "--rows", "2", "--cols", "4", "--pick", "6",
                     "--out", str(selected), "--all", str(all_dir)]) == 0
    assert Image.open(selected).getpixel((0, 0)) == (5, 0, 0)
    assert len(list(all_dir.glob("panel-*.png"))) == 8
    assert str(selected) in capsys.readouterr().out


def test_crop_grid_rejects_out_of_range_pick(tmp_path, capsys):
    source = tmp_path / "options.png"
    Image.new("RGB", (2, 2)).save(source)
    assert cli.main(["image", "crop-grid", str(source), "--rows", "2", "--cols", "2", "--pick", "5", "--out", str(tmp_path / "x.png")]) == 2
    assert "between 1 and 4" in capsys.readouterr().err
