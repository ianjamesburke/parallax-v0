# Block 4 — `tools_video.py` characterization tests + progressive extraction

## Goal

`src/parallax/tools_video.py` is 1,979 lines across 25 functions. It's the
single biggest file in the repo and currently the riskiest to touch
because most of it has zero direct test coverage. The job is to (a)
build a test harness that locks in the *current* behaviour of every
public surface, then (b) split the monolith into focused modules,
running the harness after each move so any regression surfaces
immediately.

Do NOT skip step (a). A refactor without characterization tests is a
gamble — and most of the bugs we'd cause wouldn't surface until someone
runs an end-to-end produce and *visually* notices something off, which
isn't a feedback loop you can iterate on.

## State at handoff

- Branch: `refactor/openrouter-cli` (10 commits ahead of `main`)
- Most recent commit: `cd18774` — WhisperX forced alignment + caption refactor
- Tests: **92 passed, 1 skipped, 1 warning**
  - With direct coverage in `tools_video.py`: `align_scenes` (7),
    `_smart_chunk_words` (8), `_expand_animation_keyframes` (8),
    `resolve_caption_style` (9)
  - Everything else in the file: **zero direct tests**
- The full produce pipeline (`uv run parallax produce ...`) is the only
  thing that exercises the un-tested code paths today, and only
  end-to-end.

## Phase 1 — write characterization tests

Goal: every public function in `tools_video.py` has at least one direct
test that locks in *current* behaviour. Snapshot-style is fine —
input → assert specific output. Do not refactor anything during this
phase. Just describe what's there.

### What to cover

The 21 currently-untested functions, grouped by domain:

**Project setup**
- `scan_project_folder(folder_path)` → JSON shape, ken_burns vs
  video_clips mode detection, character_image vs numbered clips.

**Voiceover**
- `generate_voiceover(text, voice, speed, out_dir, style, style_hint)`
  — mock the underlying OpenRouter / Gemini / ElevenLabs calls; verify
  it routes correctly by voice prefix (`eleven:` → ElevenLabs path,
  alias starting `gemini` → Gemini, else → openrouter TTS) and that
  atempo + trim_long_pauses run after synthesis.
- `_apply_atempo(raw_path, words, out_path, speed)` — verify
  word timestamps shift by `1/speed` factor, output duration scales,
  ffmpeg call has the right `atempo=` filter.
- `_trim_long_pauses(audio_path, words, out_path, max_gap_s,
  keep_gap_s)` — feed words with a known 2s gap, assert the gap
  collapses to `keep_gap_s`, the keep segments concat in order, and
  word timestamps shift back by the cumulative removed seconds.

**Animation**
- `animate_scenes(scenes_json, ...)` — mock the openrouter video call;
  verify it skips scenes that already have `clip_path`, calls per-scene
  with the right reference image, writes outputs to expected paths.

**Assembly**
- `ken_burns_assemble(scenes_json, audio_path, output_path,
  resolution)` — feed two scenes with known stills + a 2s wav, assert
  the output mp4 has expected duration (= sum of scene durations) and
  that the audio stream length matches the wav (regression test for
  the trailing-tail clip we just fixed).
- `_zoom_filter(direction, zoom_amount, duration, w, h, fps)` — assert
  the produced filter string for each direction (`up`/`down`/`left`/
  `right`/`in`/`None`). String-snapshot test.
- `_make_kb_clip(still, dur, out, resolution, scene_index,
  zoom_direction, zoom_amount)` — assert output exists, has correct
  duration, correct resolution.

**Clip-mode assembly**
- `assemble_clip_video(...)` and `_make_clip_segment(...)` — at
  minimum, snapshot the ffmpeg command list they build for a known
  scene set.

**Captions**
- `_style_drawtext_filter(style, text, start, end, fontsize)` — string
  snapshot for each preset (bangers, anton, clean, …).
- `_burn_captions_drawtext(video_path, chunks, out, fontsize, style)`
  — given a 1s test video + 2 chunks, verify the output exists and
  drawtext filter graph is correct.
- `_burn_captions_pillow(...)` — same, exercising the fallback path.

**Headline / titles / avatar**
- `burn_titles`, `burn_headline`, `generate_avatar_clips`,
  `key_avatar_track`, `burn_avatar` — each one needs a happy-path
  test that asserts the output file exists and has expected duration.
  For the avatar chain, the chroma-key + composite is the most
  fragile part (we shipped a fix on `5af273d` for that — keep that
  bug from coming back).

**Manifest**
- `write_manifest`, `read_manifest` — round-trip a small dict, verify
  shape is preserved.

### Test technique — black-box, ffmpeg-real

Most of these need real ffmpeg runs to produce real artefacts, then
assert on the artefacts. **Do not mock ffmpeg.** Mocking ffmpeg gives
green tests that don't actually catch the kinds of breakage that
matter (filter graph syntax errors, audio sync, duration mismatches).

For tests that need a "video" or "still" or "wav" input, generate them
inline with ffmpeg's `lavfi` source filters:

```python
# 2-second silent wav at 44.1kHz mono
subprocess.run([
    "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=cl=mono:r=44100",
    "-t", "2", str(tmp_path / "silence.wav"),
], check=True)

# 1080x1920 red still PNG
subprocess.run([
    "ffmpeg", "-y", "-f", "lavfi", "-i", "color=red:s=1080x1920",
    "-frames:v", "1", str(tmp_path / "red.png"),
], check=True)
```

Use `tmp_path` (pytest fixture) for all output. Assert on:
- File exists + non-zero size
- Duration via `ffprobe -show_entries format=duration`
- For video: probe video stream + audio stream durations separately
- For specific filter behaviour: regex-match the filter graph from
  `ffmpeg -loglevel debug` if needed (rare — usually file-level
  asserts are enough)

### What to mock

Network calls only. OpenRouter, Gemini API, ElevenLabs, WhisperX
(the model load is slow and we already test `forced_align` separately).
Use `monkeypatch.setattr` on the module function, not on httpx — the
shim layer is what matters. Pattern is already established in
`test_openrouter.py` and `test_gemini_tts.py` — copy that style.

### Avatar chroma-key — handle with care

The avatar pipeline (`generate_avatar_clips` → `key_avatar_track` →
`burn_avatar`) is the most prone-to-regression chunk in the file. We
shipped a ProRes color-range fix in commit `0241c22`. Before refactoring
anything, write a test that exercises the full chroma-key path
end-to-end: generate a fake blue-screen avatar (lavfi color source +
audio), key it, burn it onto a base video. Assert the output exists
and has the right duration. Don't try to assert on pixel values —
just make sure the chain doesn't break.

### Acceptance — Phase 1 done when

- 92 → ~140 tests (~50 new, one or two per public function)
- `uv run pytest -q` passes
- Each test runs in <2s. If a test takes >5s (real ffmpeg
  encode/decode), that's expected for the assembly tests.
- No mocking of ffmpeg/ffprobe themselves — real subprocess calls
- Run with `PARALLAX_TEST_MODE=1` set in conftest if needed to skip
  any expensive paths

Commit with message:
```
test: characterization coverage for tools_video.py monolith

50 new tests locking in current behaviour of every public function in
tools_video.py before refactoring it. Black-box style — real ffmpeg,
inline lavfi sources, ffprobe assertions on output artefacts. Mocks
limited to network calls (OpenRouter, Gemini, ElevenLabs, WhisperX).
```

## Phase 2 — progressive extraction

Once Phase 1 is green, split the monolith **one module at a time** and
run the full suite after each move. The order matters — extract the
leaf-most modules first so dependencies flow outward, not inward.

### Target module layout

```
src/parallax/
  ├── tools_video.py        # gradually shrinks; eventually deleted
  ├── captions/
  │     ├── __init__.py     # re-export public surface
  │     ├── styles.py       # CAPTION_STYLES, resolve_caption_style
  │     ├── chunker.py      # _smart_chunk_words
  │     ├── animation.py    # _expand_animation_keyframes
  │     ├── drawtext.py     # _style_drawtext_filter, _burn_captions_drawtext
  │     ├── pillow.py       # _burn_captions_pillow
  │     └── burn.py         # burn_captions (the orchestration entry point)
  ├── assembly.py           # ken_burns_assemble, align_scenes, _zoom_filter,
  │                         # _make_kb_clip, assemble_clip_video, _make_clip_segment
  ├── headline.py           # burn_headline, burn_titles
  ├── avatar.py             # generate_avatar_clips, key_avatar_track, burn_avatar
  ├── manifest.py           # write_manifest, read_manifest
  ├── voiceover.py          # generate_voiceover, _apply_atempo, _trim_long_pauses,
  │                         # _mock_voiceover
  ├── project.py            # scan_project_folder
  └── ffmpeg_utils.py       # _get_ffmpeg, _ffmpeg_has_drawtext, _probe_fps,
                            # _parse_color
```

`captions/` becomes a subpackage because there are 6 logical pieces.
Everything else is a single file per domain.

### Extraction recipe

For each module:

1. Create the new file with the target functions copied verbatim (NO
   refactoring — pure move).
2. Update imports inside the moved functions if they referenced sibling
   private helpers in `tools_video.py` — reach for those helpers in
   their new home.
3. In `tools_video.py`, delete the moved functions and add a
   compatibility re-export at the top:
   ```python
   # Compat shim — these used to live here. Imports throughout the
   # codebase still work, but new code should import from the
   # extracted module directly.
   from .captions.styles import CAPTION_STYLES, resolve_caption_style  # noqa: F401
   ```
4. Run `uv run pytest -q`. **All tests must pass.** If any fail, undo
   the move and investigate before retrying.
5. Update direct call sites within the codebase to import from the new
   home. Re-run tests.
6. Update `AGENTS.md` — module map section.
7. Commit each extraction separately with a tight message:
   ```
   refactor: extract captions/ subpackage from tools_video.py
   ```

### Recommended order

Leaf-most first (least likely to break): start with the modules that
have full test coverage so failures surface immediately.

1. **`captions/`** (well-tested, self-contained — easiest start)
2. **`manifest.py`** (trivially simple — confidence builder)
3. **`ffmpeg_utils.py`** (private helpers — pure functions)
4. **`assembly.py`** (now has Phase-1 tests, still touchy)
5. **`avatar.py`** (chroma-key chain — keep tests close at hand)
6. **`headline.py`**
7. **`voiceover.py`** (most cross-cutting; do last)
8. **`project.py`**
9. Delete `tools_video.py` once empty.

### Forbidden moves during Phase 2

- Don't rename functions. The shim only re-exports verbatim names.
- Don't change function signatures. If a signature feels wrong, write
  it down in `DEV_LOG.md` as `[FUTURE]` and address after the split is
  complete.
- Don't combine helpers that look duplicate-y. They might differ in
  a way that's load-bearing. Refactor only after the split lands.
- Don't add new dependencies between extracted modules. If
  `assembly.py` ends up importing from `captions/`, that's a smell —
  one of them should be talking through a thin interface, not the
  full module.

## Definition of done — Block 4

- `tools_video.py` deleted (or reduced to <100 lines of pure shims)
- Every extracted module has a `"""..."""` module docstring explaining
  what's in it and why
- All tests pass: `uv run pytest -q` → 0 failures, 0 errors
- `AGENTS.md` module map matches the actual filesystem
- `DEV_LOG.md` entry with `Breaks if:` line listing the most likely
  symptoms if the refactor regressed something:
  - Final mp4 is shorter than the wav (assembly tail-cover regression)
  - Captions display at wrong size or wrong position (drawtext
    filter graph regression)
  - Avatar appears as a blue rectangle instead of being keyed
    (chroma-key regression)
  - `parallax produce` import errors (a module move broke a
    consumer import)

## Gotchas

- The pyright-in-CLI cache is unreliable in the worktree — diagnostics
  often lag the file state. Trust `uv run pytest`, not the squiggles.
- `uv run pytest` may pick up the system Python's pytest if the venv's
  pytest isn't installed. We added `pytest` as a dev dep on
  `cd18774`; if you see `ModuleNotFoundError: No module named pytest`
  from `.venv/bin/python3`, run `uv add --dev pytest`.
- `from __future__ import annotations` is in every file. That means
  type hints are strings at runtime — fine for the most part, but
  pydantic-ai's `RunContext[Deps]` style of agent tool requires the
  generic param to actually exist at module level. (Bit us in
  Block 3.) Keep type imports out of conditional / inline imports.
- The libtorchcodec warning during `uv run pytest` is benign — whisperx
  uses its own audio loader, the torchcodec lib is dead weight that
  ships with `whisperx[diarization]`. Don't try to fix it; it'll just
  cost time.

## Time estimate

Phase 1 (characterization tests): one focused subagent session. ~50
small tests using real ffmpeg + lavfi sources. Most of the time goes
to figuring out the right `ffprobe` assertions for each function, not
writing the code. The mocking of network calls (OpenRouter, Gemini)
follows established patterns from `test_openrouter.py`.

Phase 2 (extraction): another focused session. The actual moves are
mechanical — most of the time goes to running the test suite between
moves and chasing import-graph cleanups.

Both phases together fit comfortably in one fresh-context Claude Code
session if Phase 1 finishes cleanly. If Phase 1 surfaces real bugs in
the existing code (likely, given the coverage gap), commit those fixes
separately as `[FIX]` entries in DEV_LOG with their own tests, then
return to Phase 2.
