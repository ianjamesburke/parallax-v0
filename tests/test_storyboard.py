from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest
import yaml

from parallax.brief import Brief, BriefScene
from parallax.cli import main
from parallax.plan import Plan, PlanScene
from parallax.planner import plan_from_brief
from parallax.preflight import compute_preflight
from parallax.settings import ProductionMode
from parallax.stages import PipelineState, stage_stills, stage_animate
from parallax.storyboard import render_storyboard as _render, review_storyboard


def render_storyboard(brief, folder, timeline=None):
    return _render(brief, folder, review_storyboard(brief, folder, 'brief.yaml', timeline))


def _scene(**kwargs):
    return dict(index=0, vo_text="One two three.", prompt="Visual direction", **kwargs)


def _brief(**kwargs):
    return Brief(goal="Campaign", script={"scenes": [_scene(**kwargs)]})


def test_scene_type_infers_animation_and_rejects_conflicts():
    for cls in (BriefScene, PlanScene):
        assert cls(**_scene(scene_type="animation")).animate is True
        assert cls(**_scene(scene_type="still")).animate is False
        with pytest.raises(ValueError, match="requires animate=false"):
            cls(**_scene(scene_type="still", animate=True))
        with pytest.raises(ValueError, match="requires animate=true"):
            cls(**_scene(scene_type="animation", animate=False))
    assert _brief(animate=True).script.scenes[0].resolved_scene_type == "animation"
    assert _brief().script.scenes[0].resolved_scene_type == "still"


def test_motion_graphic_allowed_for_storyboard_but_not_unlocked_plan():
    brief = _brief(scene_type="motion_graphic")
    with pytest.raises(ValueError, match="locally rendered clip_path"):
        brief.to_plan_skeleton()
    with pytest.raises(ValueError, match="locally rendered clip_path"):
        PlanScene(**_scene(scene_type="motion_graphic"))


def test_local_graphic_propagates_to_plan_and_missing_clip_blocks(tmp_path):
    source = tmp_path / 'brief.yaml'
    brief = _brief(scene_type="motion_graphic", clip_path="graphic.mp4")
    source.write_text(yaml.safe_dump(brief.to_dict()))
    result = plan_from_brief(source, tmp_path)
    assert not result.ok
    assert result.missing_assets == [str(tmp_path / 'graphic.mp4')]
    (tmp_path / 'graphic.mp4').write_bytes(b'local clip')
    result = plan_from_brief(source, tmp_path)
    plan = Plan.from_yaml(result.plan_path)
    assert plan.scenes[0].clip_path == 'graphic.mp4'
    assert plan.scenes[0].scene_type == 'motion_graphic'
    assert plan.scenes[0].animate is False


def test_motion_graphic_pipeline_never_generates_ai_assets(tmp_path, monkeypatch):
    import parallax.stages as stages
    def forbidden(*args, **kwargs):
        pytest.fail("Local motion graphics must not call an AI provider")
    monkeypatch.setattr(stages, '_generate_one_still', forbidden)
    monkeypatch.setattr(stages, '_animate_one_scene', forbidden)
    clip = tmp_path / 'graphic.mp4'
    clip.write_bytes(b'local clip')
    plan = _brief(scene_type="motion_graphic", clip_path="graphic.mp4").to_plan_skeleton()
    settings = SimpleNamespace(folder=tmp_path, aspect='9:16', image_model='mid', video_model='draft', mode=ProductionMode.TEST, events=lambda *_: None)
    state = PipelineState(stills_dir=str(tmp_path / 'stills'))
    stage_stills(plan, settings, state)
    stage_animate(plan, settings, state)
    assert state.scenes[0].clip_path == str(clip)
    assert state.scenes[0].still_path == ''
    preflight = compute_preflight(plan, folder=tmp_path)
    assert len(preflight.scenes) == 1
    assert preflight.scenes[0].model_alias == 'local'
    assert preflight.scenes[0].cost_usd == 0
    clip.unlink()
    with pytest.raises(ValueError, match='clip_path is missing'):
        compute_preflight(plan, folder=tmp_path)
    with pytest.raises(ValueError, match='missing or unusable'):
        stage_stills(plan, settings, state)


def test_html_escapes_text_embeds_references_and_distinguishes_assets(tmp_path):
    reference = tmp_path / 'style & reference.png'
    Image.new('RGB', (32, 32), '#aabbcc').save(reference)
    brief = Brief(goal='<script>alert("x")</script>', assets={"provided": [{"path": reference.name, "kind": "style_ref"}]}, script={"scenes": [
        _scene(scene_type='animation', still_path=reference.name, image_refs=['missing.png'], on_screen_text='<b>Copy</b>'),
        dict(index=1, vo_text='End', prompt='<img onerror=alert(1)>', scene_type='motion_graphic'),
    ]})
    html = render_storyboard(brief, tmp_path)
    assert f'<h1>{tmp_path.name}</h1>' in html
    assert f'<title>{tmp_path.name} · Storyboard</title>' in html
    assert '1 animation · 1 motion graphic' in html
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert '&lt;b&gt;Copy&lt;/b&gt;' in html
    assert 'data:image/jpeg;base64,' in html
    assert 'Missing asset' in html and 'missing.png' in html
    assert 'Supplied still' in html and 'Animation' in html
    assert 'not production timing' in html
    assert 'not a generated still' in html
    assert reference.as_uri().replace('&', '&amp;') in html


def test_storyboard_command_writes_default_and_custom_package_offline(tmp_path, capsys):
    source = tmp_path / 'brief.yaml'
    source.write_text(yaml.safe_dump(_brief(scene_type='motion_graphic').to_dict()))
    args = ['storyboard', '--folder', str(tmp_path), '--brief', str(source)]
    assert main(args) == 0
    assert (tmp_path / 'storyboard-package/storyboard.html').is_file()
    custom = tmp_path / 'review/custom'
    assert main([*args, '--package-dir', str(custom)]) == 0
    assert (custom / 'storyboard.html').is_file()
    assert 'No provider calls made' in capsys.readouterr().out
    assert main(['storyboard', '--folder', str(tmp_path), '--brief', str(tmp_path / 'absent.yaml')]) == 2


def test_local_graphic_full_pipeline_assembles_without_still(tmp_path, monkeypatch):
    import subprocess
    import parallax.stages as stages
    from parallax.produce import run_plan

    def forbidden(*args, **kwargs):
        pytest.fail('Local graphic must not generate images or animation')
    monkeypatch.setattr(stages, '_generate_one_still', forbidden)
    monkeypatch.setattr(stages, '_animate_one_scene', forbidden)
    clip = tmp_path / 'graphic.mp4'
    subprocess.run([
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
        '-i', 'color=c=blue:s=180x320:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip),
    ], check=True, capture_output=True)
    plan = {'resolution': '180x320', 'captions': 'skip', 'scenes': [
        dict(index=0, scene_type='motion_graphic', clip_path='graphic.mp4', duration_s=1.0),
    ]}
    source = tmp_path / 'plan.yaml'
    source.write_text(yaml.safe_dump(plan))
    result = run_plan(tmp_path, source, mode=ProductionMode.TEST, yes=True, debug_level=1)
    assert result.error is None, result.error
    assert Path(result.final_video).is_file()
    probe = subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', str(result.final_video),
    ], check=True, capture_output=True, text=True)
    assert abs(float(probe.stdout) - 1.0) < 0.2
    manifest = yaml.safe_load((Path(result.output_dir) / 'manifest.yaml').read_text())
    assert manifest['scenes'][0]['clip_path'] == str(clip)


def test_missing_graphic_prevents_other_scenes_generation(tmp_path, monkeypatch):
    import parallax.stages as stages
    def forbidden(*args, **kwargs):
        pytest.fail('Missing graphic must abort before any image jobs launch')
    monkeypatch.setattr(stages, '_generate_one_still', forbidden)
    plan = {'scenes': [_scene(), dict(index=1, scene_type='motion_graphic', clip_path='missing.mp4')]}
    settings = SimpleNamespace(folder=tmp_path, aspect='9:16', image_model='mid', mode=ProductionMode.TEST, events=lambda *_: None)
    state = PipelineState(stills_dir=str(tmp_path / 'stills'))
    with pytest.raises(ValueError, match='missing or unusable'):
        stage_stills(plan, settings, state)


def test_headline_is_opening_overlay_not_narration_or_reading_time(tmp_path):
    brief = _brief()
    brief.headline = 'This opening headline is visible and must never be read aloud.'
    source = tmp_path / 'brief.yaml'
    source.write_text(yaml.safe_dump(brief.to_dict()))
    result = plan_from_brief(source, tmp_path)
    plan = Plan.from_yaml(result.plan_path)
    assert plan.headline == brief.headline
    assert ' '.join(scene.vo_text for scene in plan.scenes) == 'One two three.'
    html = render_storyboard(brief, tmp_path)
    assert '<h2>Opening headline</h2>' in html
    assert brief.headline in html
    assert 'Not spoken; excluded from the reading estimate.' in html
    assert 'Estimated reading time: 1.2s' in html
    assert 'color-scheme:dark' in html
    assert '<nav' not in html


def test_reference_provenance_defaults_groups_status_and_escaped_url(tmp_path):
    from parallax.brief import ProvidedAsset
    legacy = ProvidedAsset(path='style.png', kind='style_ref')
    assert legacy.origin == 'supplied'
    assert legacy.review_status == 'pending'
    brief = _brief()
    brief.assets.provided = [legacy,
        ProvidedAsset(path='character.png', kind='character_ref', origin='generated'),
        ProvidedAsset(path='logo.png', kind='product_ref', origin='official', review_status='approved',
                      source_url='https://example.com/brand?name="quoted"&x=1'),
    ]
    html = render_storyboard(brief, tmp_path)
    assert 'Source style and supplied references' in html
    assert 'Generated character and style references' in html
    assert 'Official brand references' in html
    assert 'Generated · Review: pending' in html
    assert 'Official · Review: approved' in html
    assert 'href="https://example.com/brand?name=&quot;quoted&quot;&amp;x=1"' in html
    for url in ('javascript:alert(1)', 'file:///tmp/logo.png', 'https:///missing-host'):
        with pytest.raises(ValueError, match=r'http\(s\) URL'):
            ProvidedAsset(path='logo.png', kind='product_ref', source_url=url)


def test_storyboard_flags_unanchored_editorial_scenes_and_graphic_repetition(tmp_path):
    def scene(index, **kwargs):
        prompt = kwargs.pop('prompt', None)
        payload = _scene(**kwargs)
        payload['index'] = index
        if prompt is not None:
            payload['prompt'] = prompt
        return payload
    brief = Brief(goal='Campaign', script={'scenes': [
        scene(0, scene_type='still'),
        scene(1, scene_type='motion_graphic'),
        scene(2, scene_type='motion_graphic'),
        scene(3, scene_type='motion_graphic', prompt='Show the app logo and product UI'),
    ]})
    html = render_storyboard(brief, tmp_path)
    assert 'Reference and visual-variety review' in html
    assert 'no explicit image reference' in html
    assert 'Product/brand-dependent scene(s) 03 have no assigned reference' in html
    assert '3 of 4 scenes are motion graphics (75%)' in html
    assert 'Consecutive motion-graphic run: 01, 02, 03' in html
    assert 'No scene image reference' in html


def test_storyboard_flags_overflow_copy_and_compound_generated_frame(tmp_path):
    brief = Brief(goal='Campaign', script={'scenes': [{
        'index': 0,
        'vo_text': 'One two three.',
        'prompt': 'Dynamic diptych: the same adult turns off a bedside light, then wakes alert into sunrise.',
        'on_screen_text': 'NO AFTERNOON CRASH?',
    }]})
    html = render_storyboard(brief, tmp_path)
    assert '9:16 safe-layout heuristic' in html
    assert 'Compound visual direction' in html
    assert 'do not ask a single image generation to depict multiple scenes or moments' in html


def test_concept_name_and_id_identify_storyboard_without_changing_production(tmp_path):
    brief = _brief()
    brief.concept_name = "A better <morning>"
    brief.concept_id = "REF-02 & A"
    html = render_storyboard(brief, tmp_path)
    assert '<h1>A better &lt;morning&gt;</h1>' in html
    assert '<title>A better &lt;morning&gt; · REF-02 &amp; A · Storyboard</title>' in html
    assert 'Concept ID: REF-02 &amp; A' in html
    assert brief.goal in html
    assert 'concept_name' not in brief.to_plan_skeleton()
    assert 'concept_id' not in brief.to_plan_skeleton()
