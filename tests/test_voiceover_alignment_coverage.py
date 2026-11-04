import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from parallax.stages import PipelineState, stage_voiceover


SCRIPT_WORDS = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot', 'golf', 'hotel', 'india', 'juliet', 'kilo']


def words():
    return [{'word': word, 'start': round(i * 2.8, 3), 'end': round(i * 2.8 + 0.8, 3)} for i, word in enumerate(SCRIPT_WORDS)]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    audio = tmp_path / 'voice.mp3'
    audio.write_bytes(b'audio')
    monkeypatch.setattr('parallax.stages.is_test_mode', lambda: False)
    monkeypatch.setattr('parallax.stages.run_ffmpeg', lambda *a, **kw: SimpleNamespace(stdout='30.0', returncode=0))
    # Exercise cut's real overlapping-window stitcher without invoking ffmpeg.
    def fake_ffmpeg(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b'window')
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr('parallax.cut.run_ffmpeg', fake_ffmpeg)
    plan = {'audio_path': 'voice.mp3', 'scenes': [{'vo_text': ' '.join(SCRIPT_WORDS)}]}
    settings = SimpleNamespace(folder=tmp_path, events=lambda *a: None)
    state = PipelineState(audio_dir=str(tmp_path / 'output'))
    digest = hashlib.sha256(b'audio').hexdigest()[:20]
    cache = tmp_path / 'parallax' / 'audio' / f'aligned_{digest}.json'
    return plan, settings, state, cache


def test_incomplete_alignment_retries_whole_file_and_windows(setup, monkeypatch):
    plan, settings, state, cache = setup
    calls = []
    def align(path):
        calls.append(path)
        if Path(path).name.startswith('win'):
            start = 0 if Path(path).name == 'win000.wav' else 24
            return [{**w, 'start': w['start'] - start, 'end': w['end'] - start}
                    for w in words() if start <= w['start'] < start + 27]
        return words()[:5]
    monkeypatch.setattr('parallax.forced_align.align_words', align)
    stage_voiceover(plan, settings, state)
    assert len(calls) == 4  # initial, whole-file retry, two windows
    assert json.loads(cache.read_text())['words'] == words()
    assert state.vo_result['total_duration_s'] == 30.0


def test_incomplete_after_all_retries_leaves_no_cache(setup, monkeypatch):
    plan, settings, state, cache = setup
    monkeypatch.setattr('parallax.forced_align.align_words', lambda _: words()[:2])
    with pytest.raises(ValueError, match='Incomplete voiceover alignment.*Rerun'):
        stage_voiceover(plan, settings, state)
    assert not cache.exists()


def test_stale_incomplete_cache_is_replaced(setup, monkeypatch):
    plan, settings, state, cache = setup
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({'words': words()[:4]}))
    calls = []
    def align(path):
        calls.append(path)
        return words()
    monkeypatch.setattr('parallax.forced_align.align_words', align)
    stage_voiceover(plan, settings, state)
    assert len(calls) == 1
    assert json.loads(cache.read_text())['words'] == words()


def test_full_coverage_with_wrong_script_is_rejected(setup, monkeypatch):
    plan, settings, state, cache = setup
    wrong = [{**w, 'word': 'wrong'} for w in words()]
    monkeypatch.setattr('parallax.forced_align.align_words', lambda _: wrong)
    with pytest.raises(ValueError, match='does not match the plan narration'):
        stage_voiceover(plan, settings, state)
    assert not cache.exists()


def test_stale_cache_removed_when_retries_fail(setup, monkeypatch):
    plan, settings, state, cache = setup
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({'words': words()[:3]}))
    monkeypatch.setattr('parallax.forced_align.align_words', lambda _: words()[:2])
    with pytest.raises(ValueError, match='Incomplete voiceover alignment'):
        stage_voiceover(plan, settings, state)
    assert not cache.exists()


def test_initial_empty_alignment_error_is_retried(setup, monkeypatch):
    plan, settings, state, cache = setup
    attempts = []
    def align(path):
        attempts.append(path)
        if len(attempts) == 1:
            raise RuntimeError('produced 0 words')
        return words()
    monkeypatch.setattr('parallax.forced_align.align_words', align)
    stage_voiceover(plan, settings, state)
    assert len(attempts) == 2 and cache.exists()
