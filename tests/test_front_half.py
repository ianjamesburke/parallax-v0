import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from parallax.cli import app
from parallax.front_half import import_media, check_folder
from parallax.storyboard_package import write_package


def make_project(tmp_path):
    (tmp_path / 'references').mkdir()
    (tmp_path / 'storyboard-package').mkdir()
    (tmp_path / 'script.txt').write_text('One two three.\n')
    (tmp_path / 'brief.yaml').write_text(yaml.safe_dump({'goal': 'Story', 'script': {'scenes': [{'index': 0, 'vo_text': 'One two three.', 'prompt': 'A quiet landscape', 'generation_method': 'external'}]}}))


def test_text_only_timing_and_pdf(tmp_path, monkeypatch):
    make_project(tmp_path)
    def render(html, pdf):
        Path(pdf).write_bytes(b'pdf')
        return True
    monkeypatch.setattr('parallax.storyboard_package.render_pdf', render)
    result = write_package(tmp_path / 'brief.yaml', tmp_path, tmp_path / 'storyboard-package', pdf=True, text_only=True)
    scene = yaml.safe_load((tmp_path / 'storyboard-package/scenes.yaml').read_text())['scenes'][0]
    assert scene['scene_id'] == 'Scene_01'
    assert scene['duration_s'] == 1 and scene['min_usable_s'] == 2
    assert not result['missing_assets']
    assert 'Generation prompt' in (tmp_path / 'storyboard-package/storyboard.html').read_text()


def test_import_and_validation(tmp_path):
    make_project(tmp_path)
    source = tmp_path / 'incoming'
    source.mkdir()
    (source / 'Scene_01_hash.mp4').write_bytes(b'1')
    (source / 'Scene_01_other.mp4').write_bytes(b'2')
    imported = import_media(str(tmp_path), str(source))
    assert [x['filename'] for x in imported['takes']] == ['Scene_01.mp4', 'Scene_01_take02.mp4']
    assert imported['takes'][0]['source_filename'] == 'Scene_01_hash.mp4'
    assert any(e.get('id') == 'ambiguous_take' for e in check_folder(str(tmp_path))['findings'])
    (tmp_path / 'script.txt').write_text('Different words')
    result = CliRunner().invoke(app, ['check', '--folder', str(tmp_path), '--json'])
    assert result.exit_code != 0
    assert any(e['id'] == 'script_mismatch' for e in json.loads(result.stdout)['findings'])


def test_guide_teaches_front_half():
    result = CliRunner().invoke(app, ['guide'])
    text = result.stdout
    for phrase in ('brief.yaml', 'parallax check --folder PROJECT', 'storyboard --folder PROJECT --text-only --pdf', 'human for approval BEFORE any media generation', 'assets import', '--stage media', 'before handoff'):
        assert phrase in text


def test_check_exit_codes(tmp_path):
    make_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ['check', '--folder', str(tmp_path), '--json'])
    assert result.exit_code == 0
    assert any(f["id"] == "external_style_reference" and f["severity"] == "warning" for f in json.loads(result.stdout)["findings"])
    (tmp_path / 'script.txt').write_text('wrong')
    result = runner.invoke(app, ['check', '--folder', str(tmp_path), '--json'])
    assert result.exit_code == 2
    assert all({'id', 'severity', 'message', 'fix'} <= set(f) for f in json.loads(result.stdout)['findings'])


def test_media_duration_check(tmp_path, monkeypatch):
    import subprocess
    make_project(tmp_path)
    media = tmp_path / 'parallax/assets'
    media.mkdir(parents=True)
    (media / 'Scene_01.mp4').write_bytes(b'clip')
    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/ffprobe')
    monkeypatch.setattr('subprocess.run', lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, '1.5\n', ''))
    result = check_folder(str(tmp_path), 'media')
    assert any(f['id'] == 'clip_short' for f in result['findings'])
    assert result['exit_code'] == 2


def test_external_methods_and_brand_gates(tmp_path):
    make_project(tmp_path)
    path = tmp_path / 'brief.yaml'
    data = yaml.safe_load(path.read_text())
    for method in ('external', 'direct_clip', 'provided'):
        data['script']['scenes'][0]['generation_method'] = method
        path.write_text(yaml.safe_dump(data))
        result = check_folder(tmp_path)
        assert result['exit_code'] == 0
        assert any(f['id'] == 'external_style_reference' for f in result['findings'])
    data['script']['scenes'][0]['prompt'] = 'Show the product logo'
    path.write_text(yaml.safe_dump(data))
    result = check_folder(tmp_path)
    assert result['exit_code'] == 0
    assert not any(f['id'].startswith('brand_ref_') for f in result['findings'])
    assert all('resolve the decision' not in f['fix'].lower() for f in result['findings'])


def test_text_only_presentation(tmp_path):
    make_project(tmp_path)
    path = tmp_path / 'brief.yaml'
    data = yaml.safe_load(path.read_text())
    data['title'] = 'My project'
    data['script']['scenes'][0]['prompt'] = 'Invoices.'
    path.write_text(yaml.safe_dump(data))
    write_package(path, tmp_path, tmp_path / 'storyboard-package', text_only=True)
    html = (tmp_path / 'storyboard-package/storyboard.html').read_text()
    assert 'Scene 01</h2>' in html
    assert '“One two three.”' in html
    assert 'Invoices.' in html
    assert 'Scene_01 · external clip' not in html
    assert 'Scene 01 · no image yet' in html and 'frame-mat' in html
    assert 'None' not in html


def test_explicit_brand_reference_statuses(tmp_path):
    make_project(tmp_path)
    path = tmp_path / 'brief.yaml'
    data = yaml.safe_load(path.read_text())
    scene = data['script']['scenes'][0]
    scene['prompt'] = 'A WellyBox app screen with logo'
    scene['brand_ref'] = {'status': 'missing', 'note': 'WellyBox app screen'}
    path.write_text(yaml.safe_dump(data))
    result = check_folder(tmp_path)
    assert result['exit_code'] == 0
    assert any(f['id'] == 'reference_missing' and f['severity'] == 'warning' for f in result['findings'])
    assert CliRunner().invoke(app, ['check', '--folder', str(tmp_path), '--json']).exit_code == 0
    assert any('Scene 01' in f['message'] and 'WellyBox app screen' in f['message'] for f in result['findings'])
    write_package(path, tmp_path, tmp_path / 'storyboard-package', text_only=True)
    assert 'Needs brand reference' not in (tmp_path / 'storyboard-package/storyboard.html').read_text()
    scene['brand_ref'] = {'status': 'supplied', 'paths': ['references/official/app.png']}
    path.write_text(yaml.safe_dump(data))
    result = check_folder(tmp_path)
    assert result['exit_code'] == 2
    assert any(f['id'] == 'reference_file' and f['severity'] == 'error' and 'references/official/app.png' in f['message'] for f in result['findings'])
    assert CliRunner().invoke(app, ['check', '--folder', str(tmp_path), '--json']).exit_code == 2
    (tmp_path / 'references/official').mkdir()
    (tmp_path / 'references/official/app.png').write_bytes(b'image')
    assert check_folder(tmp_path)['exit_code'] == 0
    scene['brand_ref'] = {'status': 'not_needed'}
    data['goal'] = 'A vertical WellyBox ad'
    for prompt in ('logo', 'packaging', 'app screen', 'product shot', 'WellyBox on screen'):
        scene['prompt'] = prompt
        path.write_text(yaml.safe_dump(data))
        result = check_folder(tmp_path)
        assert result['exit_code'] == 0
        assert not any(f['id'].startswith('brand_ref_') for f in result['findings'])


def test_shared_style_and_guide(tmp_path):
    make_project(tmp_path)
    path = tmp_path / 'brief.yaml'
    data = yaml.safe_load(path.read_text())
    data['visual_style'] = 'Distinctive shared style'
    data['script']['scenes'][0]['storyboard_title'] = 'Hidden title'
    path.write_text(yaml.safe_dump(data))
    write_package(path, tmp_path, tmp_path / 'storyboard-package', text_only=True)
    html = (tmp_path / 'storyboard-package/storyboard.html').read_text()
    assert html.count('Distinctive shared style') == 1
    assert 'Hidden title' not in html
    guide = CliRunner().invoke(app, ['guide']).stdout
    assert 'EVERY scene' not in guide and 'references/official/' in guide and 'email inbox' in guide


def test_text_only_frame_uses_still_and_scene_aspect(tmp_path):
    from PIL import Image
    make_project(tmp_path)
    path = tmp_path / 'brief.yaml'
    data = yaml.safe_load(path.read_text())
    scene = data['script']['scenes'][0]
    scene['aspect'] = '16:9'
    scene['still_path'] = 'still.png'
    Image.new('RGB', (16, 9), 'red').save(tmp_path / 'still.png')
    path.write_text(yaml.safe_dump(data))
    write_package(path, tmp_path, tmp_path / 'storyboard-package', text_only=True)
    html = (tmp_path / 'storyboard-package/storyboard.html').read_text()
    assert 'aspect-ratio: 16 / 9' in html
    assert '<img ' in html and 'Scene 01 · no image yet' not in html
