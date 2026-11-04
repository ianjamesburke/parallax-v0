import json
from types import SimpleNamespace

import pytest
import yaml

from parallax.brief import Brief
from parallax.front_half import check_folder, import_media
from parallax.planner import plan_from_brief
from parallax.preflight import compute_preflight, format_preflight
from parallax.stages import PipelineState, stage_voiceover, stage_stills, stage_animate
from parallax.settings import ProductionMode


def project(tmp_path):
    (tmp_path / 'references').mkdir()
    (tmp_path / 'storyboard-package').mkdir()
    (tmp_path / 'script.txt').write_text('organized—and his wife')
    data = {'goal': 'test', 'audio_path': 'vo.mp3', 'words_path': 'words.json', 'script': {'scenes': [
        {'index': 0, 'scene_id': 'Scene_01', 'vo_text': 'organized—', 'prompt': 'one', 'generation_method': 'external'},
        {'index': 1, 'scene_id': 'Scene_02', 'vo_text': 'and his wife', 'prompt': 'two', 'generation_method': 'external'}]}}
    (tmp_path / 'vo.mp3').write_bytes(b'audio')
    (tmp_path / 'words.json').write_text('{}')
    (tmp_path / 'brief.yaml').write_text(yaml.safe_dump(data))
    return data


def test_import_plan_selection_and_audio_roundtrip(tmp_path):
    data = project(tmp_path)
    source = tmp_path / 'incoming'; source.mkdir()
    for name in ('Scene_01_a.mp4', 'Scene_02_a.mp4', 'Scene_02_b.mp4'):
        (source / name).write_bytes(b'clip')
    imported = import_media(str(tmp_path), str(source))
    with pytest.raises(ValueError, match='Scene_02.*preferred'):
        plan_from_brief(tmp_path / 'brief.yaml', tmp_path)
    manifest = tmp_path / 'parallax/assets/import-manifest.json'
    records = imported['takes']; records[-1]['preferred'] = True
    manifest.write_text(json.dumps(records))
    for _ in range(2):
        result = plan_from_brief(tmp_path / 'brief.yaml', tmp_path)
        plan = yaml.safe_load(result.plan_path.read_text())
        assert plan['audio_path'] == 'vo.mp3' and plan['words_path'] == 'words.json'
        assert plan['voice_speed'] == 1.15 and plan['audio_supplied'] is True
        assert plan['scenes'][0]['clip_path'].endswith('Scene_01.mp4')
        assert plan['scenes'][1]['clip_path'].endswith('Scene_02_take02.mp4')
    data['script']['scenes'][1]['clip_path'] = 'custom.mp4'
    (tmp_path / 'custom.mp4').write_bytes(b'x')
    (tmp_path / 'brief.yaml').write_text(yaml.safe_dump(data))
    result = plan_from_brief(tmp_path / 'brief.yaml', tmp_path)
    assert yaml.safe_load(result.plan_path.read_text())['scenes'][1]['clip_path'] == 'custom.mp4'


def test_coverage_and_unused(tmp_path):
    project(tmp_path)
    assert not any(f['id'] == 'script_mismatch' for f in check_folder(tmp_path)['findings'])
    (tmp_path / 'script.txt').write_text('organized—and her wife')
    assert any(f['id'] == 'script_mismatch' for f in check_folder(tmp_path)['findings'])
    media = tmp_path / 'parallax/assets'; media.mkdir(parents=True)
    (media / 'Scene_03.mp4').write_bytes(b'x')
    assert any(f['id'] == 'unused_clips' and 'Scene_03' in f['message'] for f in check_folder(tmp_path)['findings'])


def test_supplied_clip_skips_stages(tmp_path, monkeypatch):
    clip = tmp_path / 'clip.mp4'; clip.write_bytes(b'clip')
    plan = {'audio_path': 'vo.mp3', 'scenes': [{'index': 0, 'clip_path': str(clip), 'animate': True}]}
    assert compute_preflight(plan).estimated_total_usd == 0
    settings = SimpleNamespace(folder=tmp_path, image_model='mid', aspect='9:16', mode=ProductionMode.REAL, stills_only=False, events=lambda *a: None)
    state = PipelineState(stills_dir=str(tmp_path / 'stills'))
    monkeypatch.setattr('parallax.stages._generate_one_still', lambda *a: pytest.fail('still generated'))
    stage_stills(plan, settings, state)
    assert state.scenes[0].clip_path == str(clip) and not state.scenes[0].animate
    stage_animate(plan, settings, state)


def test_supplied_mp3_speed_cache_and_probe(tmp_path, monkeypatch):
    (tmp_path / 'vo.mp3').write_bytes(b'mp3')
    settings = SimpleNamespace(folder=tmp_path, events=lambda *a: None)
    plan = {'audio_path': 'vo.mp3', 'audio_supplied': True, 'voice_speed': 1.15, 'scenes': [{'vo_text': 'hello'}]}
    calls = []
    def speed(src, dst, rate):
        calls.append(rate); dst.write_bytes(b'wav')
    monkeypatch.setattr('parallax.audio.speedup', speed)
    monkeypatch.setattr('parallax.cut.normalize_audio_loudness', lambda *a: None)
    monkeypatch.setattr('parallax.stages.is_test_mode', lambda: True)
    monkeypatch.setattr('parallax.stages.run_ffmpeg', lambda *a, **kw: SimpleNamespace(stdout='2.5', returncode=0))
    for _ in range(2):
        state = PipelineState(audio_dir=str(tmp_path / 'out'))
        assert stage_voiceover(plan, settings, state) is plan
        assert state.vo_result['total_duration_s'] == 2.5
    assert calls == [1.15]
    assert (tmp_path / 'vo.mp3').read_bytes() == b'mp3'


def test_preferred_take_from_brief(tmp_path):
    data = project(tmp_path)
    src = tmp_path / 'incoming'; src.mkdir()
    for name in ('Scene_01_a.mp4', 'Scene_01_b.mp4'):
        (src / name).write_bytes(b'clip')
    import_media(str(tmp_path), str(src))
    data['script']['scenes'][0]['takes'] = [{'take_id': 'take02', 'filename': 'Scene_01_take02.mp4', 'preferred': True}]
    (tmp_path / 'brief.yaml').write_text(yaml.safe_dump(data))
    result = plan_from_brief(tmp_path / 'brief.yaml', tmp_path)
    assert yaml.safe_load(result.plan_path.read_text())['scenes'][0]['clip_path'].endswith('Scene_01_take02.mp4')


def test_mp3_probe_without_speedup(tmp_path, monkeypatch):
    (tmp_path / 'vo.mp3').write_bytes(b'mp3')
    settings = SimpleNamespace(folder=tmp_path, events=lambda *a: None)
    state = PipelineState(audio_dir=str(tmp_path / 'out'))
    monkeypatch.setattr('parallax.stages.is_test_mode', lambda: True)
    probes = []
    def probe(args, **kwargs):
        probes.append(args)
        return SimpleNamespace(stdout='3.25', returncode=0)
    monkeypatch.setattr('parallax.stages.run_ffmpeg', probe)
    stage_voiceover({'audio_path': 'vo.mp3', 'voice_speed': 1.0, 'scenes': [{'vo_text': 'hello'}]}, settings, state)
    assert state.vo_result['total_duration_s'] == 3.25
    assert all(cmd[0] == 'ffprobe' for cmd in probes)
    assert state.audio_path == str(tmp_path / 'vo.mp3')


def test_missing_supplied_audio_and_words_are_validation_errors(tmp_path):
    project(tmp_path)
    from parallax.validate import validate_brief
    (tmp_path / 'vo.mp3').unlink()
    (tmp_path / 'words.json').unlink()
    result = validate_brief(tmp_path / 'brief.yaml', tmp_path)
    assert {'audio_path', 'words_path'} <= {e['field'] for e in result['errors']}
    assert set(Brief.from_yaml(tmp_path / 'brief.yaml').model_json_schema()['properties']) >= {'audio_path', 'words_path'}


def test_locked_tts_audio_does_not_respeed(tmp_path, monkeypatch):
    audio = tmp_path / 'tts.wav'; audio.write_bytes(b'original')
    words = tmp_path / 'words.json'; words.write_text(json.dumps({'words': [{'word': 'hello', 'start': 0.0, 'end': 2.0}]}))
    plan = {'audio_path': 'tts.wav', 'words_path': 'words.json', 'voice_speed': 1.15, 'scenes': [{'index': 0, 'vo_text': 'hello'}]}
    settings = SimpleNamespace(folder=tmp_path, events=lambda *a: None)
    state = PipelineState(audio_dir=str(tmp_path / 'out'))
    monkeypatch.setattr('parallax.audio.speedup', lambda *a: pytest.fail('locked audio must not be sped again'))
    monkeypatch.setattr('parallax.stages.run_ffmpeg', lambda *a, **kw: SimpleNamespace(stdout='2.5', returncode=0))
    stage_voiceover(plan, settings, state)
    assert state.audio_path == str(audio) and state.words_path == str(words)
    assert state.vo_result['total_duration_s'] == 2.5
    assert audio.read_bytes() == b'original'
    assert not list(tmp_path.rglob('supplied_*.wav'))
    assert any('voice_speed ignored' in w for w in compute_preflight(plan).warnings)


def test_supplied_audio_preflight_and_plan_schema(tmp_path):
    from parallax.plan import Plan
    data = project(tmp_path)
    plan = Brief.model_validate(data).to_plan_skeleton()
    assert Plan.model_validate(plan).audio_supplied is True
    estimate = compute_preflight(plan)
    assert not any('voice_speed ignored' in w for w in estimate.warnings)
    assert '[supplied — 1.15x, cached]' in format_preflight(estimate)
    data.pop('audio_path')
    assert 'audio_supplied' not in Brief.model_validate(data).to_plan_skeleton()


def test_supplied_words_are_scaled_once_and_cached(tmp_path, monkeypatch):
    (tmp_path / 'vo.mp3').write_bytes(b'mp3')
    source_words = tmp_path / 'words.json'
    source_words.write_text(json.dumps({'words': [{'word': 'hello', 'start': 1.15, 'end': 2.3}], 'total_duration_s': 3.45}))
    plan = {'audio_path': 'vo.mp3', 'words_path': 'words.json', 'audio_supplied': True, 'voice_speed': 1.15}
    settings = SimpleNamespace(folder=tmp_path, events=lambda *a: None)
    calls = []
    def speed(src, dst, rate):
        calls.append(rate); dst.write_bytes(b'wav')
    monkeypatch.setattr('parallax.audio.speedup', speed)
    monkeypatch.setattr('parallax.cut.normalize_audio_loudness', lambda *a: None)
    monkeypatch.setattr('parallax.stages.run_ffmpeg', lambda *a, **kw: SimpleNamespace(stdout='3.0', returncode=0))
    paths = []
    for _ in range(2):
        state = PipelineState(audio_dir=str(tmp_path / 'out'))
        stage_voiceover(plan, settings, state)
        paths.append((state.audio_path, state.words_path))
        assert state.vo_result['words'][0]['start'] == pytest.approx(1.0)
        assert state.vo_result['words'][0]['end'] == pytest.approx(2.0)
    assert paths[0] == paths[1] and calls == [1.15]
    assert json.loads(source_words.read_text())['words'][0]['start'] == 1.15


@pytest.mark.parametrize('extra_take', [False, True])
def test_unused_clips_warn_and_media_check_passes(tmp_path, monkeypatch, capsys, extra_take):
    from parallax import cli

    data = project(tmp_path)
    reference = tmp_path / 'references/style.png'
    reference.write_bytes(b'reference')
    data['assets'] = {'provided': [{'path': 'references/style.png', 'kind': 'style_ref'}]}
    for scene in data['script']['scenes']:
        scene['image_refs'] = ['references/style.png']
    (tmp_path / 'brief.yaml').write_text(yaml.safe_dump(data))
    source = tmp_path / 'incoming'
    source.mkdir()
    for number in (1, 2, 3, 27):
        (source / f'Scene_{number:02d}.mp4').write_bytes(b'clip')
    if extra_take:
        (source / 'Scene_03_option.mp4').write_bytes(b'clip')
    imported = import_media(str(tmp_path), str(source))
    assert not any(take.get('preferred') for take in imported['takes'])
    monkeypatch.setattr('parallax.front_half.shutil.which', lambda _: '/test/ffprobe')
    monkeypatch.setattr('subprocess.run', lambda *a, **kw: SimpleNamespace(stdout='60', returncode=0))

    result = check_folder(tmp_path, stage='media')
    warnings = [f for f in result['findings'] if f['severity'] == 'warning']
    assert len(warnings) == 1
    assert warnings[0]['id'] == 'unused_clips'
    assert warnings[0]['message'] == 'Unused clips (no brief scene): Scene_03, Scene_27. They stay in the folder.'
    assert warnings[0]['blocked'] is False
    assert 'add' not in warnings[0]['fix'].lower()
    assert not any(f['severity'] == 'error' for f in result['findings'])
    assert result['valid'] is True
    assert result['status'] == 'ok'
    assert result['exit_code'] == 0
    assert cli.main(['check', '--folder', str(tmp_path), '--stage', 'media']) == 0
    output = capsys.readouterr().out
    assert 'WARNING [unused_clips]' in output
    assert warnings[0]['message'] in output
    assert 'Project check: ok.' in output
    planned = plan_from_brief(tmp_path / 'brief.yaml', tmp_path)
    plan = yaml.safe_load(planned.plan_path.read_text())
    assert [scene['clip_path'].split('/')[-1] for scene in plan['scenes']] == [
        'Scene_01.mp4', 'Scene_02.mp4',
    ]
    if extra_take:
        assert (tmp_path / 'parallax/assets/Scene_03_take02.mp4').is_file()


def test_plan_uses_only_brief_scene_subset(tmp_path):
    data = project(tmp_path)
    data['script']['scenes'].append({
        'index': 2, 'scene_id': 'Scene_04', 'vo_text': 'arrived',
        'prompt': 'four', 'generation_method': 'external',
    })
    (tmp_path / 'script.txt').write_text('organized—and his wife arrived')
    (tmp_path / 'brief.yaml').write_text(yaml.safe_dump(data))
    source = tmp_path / 'incoming'
    source.mkdir()
    for number in (1, 2, 3, 4):
        (source / f'Scene_{number:02d}.mp4').write_bytes(b'clip')
    import_media(str(tmp_path), str(source))

    result = plan_from_brief(tmp_path / 'brief.yaml', tmp_path)
    plan = yaml.safe_load(result.plan_path.read_text())
    assert [scene['vo_text'] for scene in plan['scenes']] == ['organized—', 'and his wife', 'arrived']
    assert [scene['clip_path'].split('/')[-1] for scene in plan['scenes']] == [
        'Scene_01.mp4', 'Scene_02.mp4', 'Scene_04.mp4',
    ]
    assert (tmp_path / 'parallax/assets/Scene_03.mp4').is_file()



@pytest.mark.parametrize('with_brief', [False, True])
def test_ambiguous_used_takes_still_error(tmp_path, with_brief):
    from parallax.front_half import media_names

    data = project(tmp_path)
    source = tmp_path / 'incoming'
    source.mkdir()
    for name in ('Scene_01_a.mp4', 'Scene_01_b.mp4'):
        (source / name).write_bytes(b'clip')
    import_media(str(tmp_path), str(source))
    context = {'root': tmp_path, 'stage': 'media'}
    if with_brief:
        context['brief'] = Brief.model_validate(data)

    findings = list(media_names(context))
    assert len(findings) == 1
    assert findings[0]['id'] == 'ambiguous_take'
    assert findings[0]['severity'] == 'error'
    assert findings[0]['blocked'] is True
