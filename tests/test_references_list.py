import json
import yaml
from typer.testing import CliRunner
from parallax.cli import app
from parallax.brief import Brief
from parallax.front_half import check_folder
from parallax.storyboard import render_storyboard, review_storyboard


def project(tmp_path):
    (tmp_path / 'references').mkdir()
    (tmp_path / 'storyboard-package').mkdir()
    data = {'goal': 'Test', 'references': [{'id': 'wellybox_ui', 'kind': 'app_ui', 'description': 'WellyBox app UI', 'status': 'missing', 'paths': []}], 'script': {'scenes': [{'index': 0, 'scene_id': 'Scene_06', 'vo_text': 'Hello', 'prompt': 'Screen', 'generation_method': 'external', 'refs': ['wellybox_ui']}, {'index': 1, 'scene_id': 'Scene_10', 'vo_text': 'More', 'prompt': 'Inbox', 'generation_method': 'external'}]}}
    (tmp_path / 'brief.yaml').write_text(yaml.safe_dump(data))
    return data


def test_references_check_and_storyboard(tmp_path):
    data = project(tmp_path); path = tmp_path / 'brief.yaml'
    result = check_folder(tmp_path)
    assert result['exit_code'] == 0
    assert any(f['severity'] == 'warning' and f['message'] == 'References needed: WellyBox app UI (Scene 06)' for f in result['findings'])
    brief = Brief.from_yaml(path)
    html = render_storyboard(brief, tmp_path, review_storyboard(brief, tmp_path, path), text_only=True)
    assert 'References needed: WellyBox app UI (Scene 06)' in html and 'Refs: WellyBox app UI' in html
    assert 'Refs: None' not in html
    data['references'][0].update(status='supplied', paths=['references/no.png'])
    path.write_text(yaml.safe_dump(data))
    result = check_folder(tmp_path)
    assert result['exit_code'] == 2 and any('wellybox_ui' in f['message'] and 'references/no.png' in f['message'] for f in result['findings'])
    data['script']['scenes'][0]['refs'] = ['unknown']
    path.write_text(yaml.safe_dump(data))
    assert check_folder(tmp_path)['exit_code'] == 2


def test_legacy_and_docs(tmp_path):
    data = project(tmp_path); path = tmp_path / 'brief.yaml'
    data.pop('references'); scene = data['script']['scenes'][0]; scene.pop('refs'); scene['brand_ref'] = {'status': 'missing', 'note': 'WellyBox app UI'}
    path.write_text(yaml.safe_dump(data)); brief = Brief.from_yaml(path)
    assert brief.references[0].id == 'wellybox_app_ui' and scene['brand_ref']['status'] == brief.references[0].status
    assert brief.script.scenes[0].refs == ['wellybox_app_ui']
    assert any(f['id'] == 'legacy_brand_ref' for f in check_folder(tmp_path)['findings'])
    runner = CliRunner()
    for args in (['guide'], ['guide', 'front_half'], ['schema', 'brief']):
        output = runner.invoke(app, args).stdout
        assert 'email inbox' in output and 'wellybox_ui' in output and 'brand_ref' not in output


def test_pdf_reference_text(tmp_path):
    import shutil
    import subprocess
    import pytest
    from parallax.storyboard_package import find_pdf_renderer, write_package
    if not find_pdf_renderer() or not shutil.which('pdftotext'):
        pytest.skip('PDF renderer and pdftotext required')
    project(tmp_path)
    write_package(tmp_path / 'brief.yaml', tmp_path, tmp_path / 'storyboard-package', pdf=True, text_only=True)
    text = subprocess.run(['pdftotext', '-layout', str(tmp_path / 'storyboard-package/storyboard.pdf'), '-'], check=True, capture_output=True, text=True).stdout
    pages = text.split('\f')
    assert 'References needed: WellyBox app UI (Scene 06)' in pages[0]
    assert 'Refs: WellyBox app UI' in pages[1]
    assert 'Refs: None' not in text
