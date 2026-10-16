import json
from types import SimpleNamespace

import pytest
import yaml

from parallax.brief import Brief
from parallax.cli import main
from parallax.pacing import estimate_timeline, review_opening_pacing
from parallax.storyboard import render_storyboard
from parallax.stages import _report_opening_pacing
from parallax.validate import validate_brief, validate_plan


def _timeline(edges):
    return [{'index': i, 'start_s': a, 'end_s': b} for i, (a, b) in enumerate(zip(edges, edges[1:]))]


def test_opening_rule_boundaries_are_advisory_and_opening_only():
    report = review_opening_pacing(_timeline([0, 3, 6, 9, 12, 20]), 'aligned')
    assert [w['code'] for w in report['warnings']] == ['opening_few_cuts']
    assert report['cuts_before_6s'] == 1  # cut at exactly 6 is outside opening window
    report = review_opening_pacing(_timeline([0, 1.5, 3, 4.5, 6, 12, 20]), 'aligned')
    assert report['cuts_before_6s'] == 3
    assert [w['scene_index'] for w in report['warnings']] == [4]
    assert all(w['code'] == 'opening_long_hold' for w in report['warnings'])
    assert not review_opening_pacing(_timeline([0, 2]), 'aligned')['warnings']


def test_actual_audio_onset_advisory_distinguishes_alignment_from_acoustics():
    for start, expected in [(0.1, False), (.331, True)]:
        result = review_opening_pacing(_timeline([0, 2]), 'aligned', first_word_start_s=start)
        assert bool(result['warnings']) is expected
    assert 'not an acoustic onset' in result['warnings'][0]['message']
    assert 'trim-leading-quiet --first-word-by 0.1' in result['warnings'][0]['message']


def test_native_alignment_report_is_structured_and_does_not_mutate_timeline(monkeypatch):
    import parallax.stages as stages
    events = []
    monkeypatch.setattr(stages.runlog, 'event', lambda name, **kwargs: events.append((name, kwargs)))
    timeline = _timeline([0, 5, 9])
    before = json.dumps(timeline)
    _report_opening_pacing(timeline, SimpleNamespace(events=lambda *_: None), 'aligned', .331)
    assert json.dumps(timeline) == before
    report = next(fields for event, fields in events if event == 'qa.opening_pacing')
    assert report['timing_basis'] == 'aligned'
    assert {w['code'] for w in report['warnings']} == {'opening_long_hold', 'opening_few_cuts', 'opening_delayed_first_word'}


def test_validate_machine_warnings_use_estimated_or_declared_basis(tmp_path):
    text = ' '.join(['word'] * 25)
    scene = {'index': 0, 'vo_text': text, 'prompt': 'A portrait.'}
    brief_path = tmp_path / 'brief.yaml'
    brief_path.write_text(yaml.safe_dump({'goal': 'Campaign', 'script': {'scenes': [scene]}}))
    result = validate_brief(brief_path, tmp_path)
    assert result['valid']
    assert {w['code'] for w in result['warnings']} == {'opening_long_hold', 'opening_few_cuts'}
    assert result['opening_pacing']['timing_basis'].startswith('estimated')
    plan_path = tmp_path / 'plan.yaml'
    plan_path.write_text(yaml.safe_dump({'scenes': [{**scene, 'duration_s': 7.0}]}))
    result = validate_plan(plan_path, tmp_path)
    assert result['valid']
    assert result['opening_pacing']['timing_basis'] == 'declared scene durations'


def test_storyboard_supplied_timing_replaces_estimates_and_rejects_mismatch(tmp_path):
    scenes = [{'index': i, 'vo_text': ' '.join(['word'] * 15), 'prompt': 'Direction'} for i in range(4)]
    brief = Brief(goal='Campaign', script={'scenes': scenes})
    estimated = render_storyboard(brief, tmp_path)
    assert 'Opening pacing review' in estimated
    assert 'review this hold longer than 3s' in estimated
    supplied = render_storyboard(brief, tmp_path, _timeline([0, 1.5, 3, 4.5, 6]))
    assert 'Supplied timeline: 6.00s' in supplied
    assert 'No timing advisories' in supplied
    assert 'reading estimate' not in supplied
    assert 'requires looking at the shots' in supplied
    with pytest.raises(ValueError, match='indices/order'):
        render_storyboard(brief, tmp_path, _timeline([0, 2]))
    with pytest.raises(ValueError, match='contiguous'):
        render_storyboard(brief, tmp_path, _timeline([0, 1, .5, 2, 3]))
    brief_path = tmp_path / 'brief.yaml'
    brief_path.write_text(yaml.safe_dump(brief.to_dict()))
    timing_path = tmp_path / 'manifest.yaml'
    timing_path.write_text(yaml.safe_dump({'scenes': _timeline([0, 1.5, 3, 4.5, 6])}))
    assert main(['storyboard', '--folder', str(tmp_path), '--brief', str(brief_path), '--timing', str(timing_path)]) == 0
    assert 'Supplied timeline: 6.00s' in (tmp_path / 'parallax/scratch/storyboard.html').read_text()


@pytest.mark.parametrize('voiced', [True, False])
def test_produce_alignment_emits_native_pacing_review(monkeypatch, voiced):
    import parallax.stages as stages
    events = []
    monkeypatch.setattr(stages.runlog, 'event', lambda name, **kwargs: events.append((name, kwargs)))
    scene = stages.SceneRuntime(index=0, shot_type='broll', vo_text='Hello' if voiced else '',
                                prompt='', still_path='', aspect='9:16')
    state = stages.PipelineState(scenes=[scene])
    if voiced:
        state.vo_result = {'words': [{'word': 'Hello', 'start': .331, 'end': 1.0}], 'total_duration_s': 4.0}
    plan = {'scenes': [{'index': 0, 'vo_text': scene.vo_text, 'duration_s': 4.0}]}
    stages.stage_align(plan, SimpleNamespace(events=lambda *_: None), state)
    report = next(fields for event, fields in events if event == 'qa.opening_pacing')
    codes = {w['code'] for w in report['warnings']}
    assert 'opening_long_hold' in codes
    assert ('opening_delayed_first_word' in codes) is voiced
    assert state.aligned[0]['duration_s'] == 4.0


def test_opening_contact_sheet_uses_scene_previews_and_excludes_later_shots(tmp_path):
    from PIL import Image
    Image.new("RGB", (20, 30), "red").save(tmp_path / "approved.png")
    brief = Brief(goal="Campaign", script={"scenes": [
        {"index": 0, "vo_text": "First", "prompt": "First", "still_path": "approved.png"},
        {"index": 1, "vo_text": "Second", "prompt": "Second", "scene_type": "animation"},
        {"index": 2, "vo_text": "Later", "prompt": "Later"},
    ]})
    html = render_storyboard(brief, tmp_path, _timeline([0, 2, 12, 15]))
    sheet = html.split('<div class="opening-grid">', 1)[1].split('</section>', 1)[0]
    assert sheet.count('class="opening-shot"') == 2
    assert 'Scene 00' in sheet and 'Scene 01' in sheet and 'Scene 02' not in sheet
    assert 'data:image/jpeg;base64,' in sheet
    assert '2.00s start · 10.00s hold · Animation' in sheet
    assert 'No scene preview supplied' in sheet
    assert '<nav' not in html


def test_validate_uses_locked_words_without_false_estimated_opening_warning(tmp_path):
    words = [{'word': 'First.', 'start': .1, 'end': 1.0},
             {'word': 'Later.', 'start': 13.043, 'end': 17.0}]
    words_path = tmp_path / 'words.json'
    words_path.write_text(json.dumps({'words': words, 'total_duration_s': 17.5}))
    (tmp_path / 'audio.wav').write_bytes(b'locked audio; no decoding needed for offline timing')
    plan_path = tmp_path / 'plan.yaml'
    raw = {'audio_path': 'audio.wav', 'words_path': 'words.json', 'scenes': [
        {'index': 0, 'vo_text': 'First.', 'prompt': 'Opening'},
        {'index': 8, 'vo_text': 'Later.', 'prompt': 'Later'},
    ]}
    plan_path.write_text(yaml.safe_dump(raw))
    original_plan, original_words = plan_path.read_bytes(), words_path.read_bytes()
    result = validate_plan(plan_path, tmp_path)
    assert result['opening_pacing']['timing_basis'].startswith('aligned locked')
    assert not any(w.get('scene_index') == 8 for w in result['warnings'])
    assert any(w.get('scene_index') == 0 and w['duration_s'] == 13.043 for w in result['warnings'])
    assert plan_path.read_bytes() == original_plan and words_path.read_bytes() == original_words
    raw['scenes'][0]['duration_s'] = 2.0
    plan_path.write_text(yaml.safe_dump(raw))
    assert not any(w.get('scene_index') == 0 for w in validate_plan(plan_path, tmp_path)['warnings'])
    raw['scenes'][0]['vo_text'] = 'Changed narration.'
    plan_path.write_text(yaml.safe_dump(raw))
    fallback = validate_plan(plan_path, tmp_path)
    assert 'locked transcript does not match' in fallback['opening_pacing']['timing_basis']
    raw['voice_speed'] = 1.2
    plan_path.write_text(yaml.safe_dump(raw))
    assert 'audio transform settings' in validate_plan(plan_path, tmp_path)['opening_pacing']['timing_basis']


def test_storyboard_reads_first_word_only_when_supplied_timeline_contains_it(tmp_path):
    brief = Brief(goal="Campaign", script={"scenes": [{'index': 0, 'vo_text': 'Hello.', 'prompt': 'Opening'}]})
    timeline = _timeline([0, 2])
    assert 'First aligned word starts' not in render_storyboard(brief, tmp_path, timeline)
    timeline[0]['words'] = [{'word': 'Hello.', 'start': .331, 'end': 1.0}]
    html = render_storyboard(brief, tmp_path, timeline)
    assert 'First aligned word starts at 0.331s' in html
    assert 'not an acoustic onset measurement' in html
