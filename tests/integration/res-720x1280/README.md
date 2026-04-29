# res-720x1280 — canonical reference test case

This is the operator-facing reference case for `parallax verify-suite`.
Copy it (or use `parallax verify-init <name> --from
tests/integration/res-720x1280/`) when you author a new case.

## What it verifies

A two-scene plan run end-to-end in `PARALLAX_TEST_MODE=1` against the
test-mode mocks (no network, no spend). The `expected.yaml` here
exercises every schema branch the runner supports — treat it as the
worked example of the schema reference in
`src/parallax/verify_suite.py`.

| Block | Asserts |
|-------|---------|
| `final.resolution` | ffprobe of the renamed `<folder>-vN.mp4` matches `720x1280`. |
| `final.duration_s` | Total duration falls inside `[min, max]`. |
| `final.audio_video_diff_s_max` | Audio stream length tracks video length to within 0.10s. |
| `final.scene_count` | `len(manifest.scenes) == 2`. |
| `stages.stills` | At least one `stills/*.png` exists. (See note below — `stages.stills.resolution` is intentionally omitted; covered by the smoke fixture instead.) |
| `stages.voiceover` | `audio/voiceover.*` + `audio/vo_words.json` both written. |
| `stages.assemble` | A renamed `*.mp4` exists at `out_dir` root, has correct resolution, and manifest scenes are contiguous covering the full timeline (`start_s == 0`, `prev.end_s == curr.start_s`, last `end_s == total_duration`). |
| `manifest.keys_required` | `manifest.yaml` has top-level `model`, `voice`, `resolution`, `scenes`. |
| `manifest.scene_keys_required` | Every scene has `index`, `vo_text`, `prompt`, `start_s`, `end_s`, `duration_s`. |
| `run_log.must_not_contain` | JSONL run log at `<output_dir>/run.log` has no `Traceback` and no `"level": "ERROR"` lines. |
| `run_log.must_contain` | Run log contains `plan.loaded` and `run.end`. |
| `cost_usd_max` | `cost.json.cost_usd <= 0.0` — the canonical guardrail that test-mode runs are free. |

### Why `stages.stills.resolution` is omitted

The product guarantee is "final shipped mp4 is the requested
resolution." That's what `final.resolution` and `assemble.resolution`
verify (via ffprobe of the renamed `<folder>-vN.mp4`). Per-stage
stills resolution is an internal detail.

Test-mode stills are produced via `shim.render_mock_image`, which
honors the resolution derived from `aspect_ratio` (or an explicit
`size`). Real-mode stills DO honor the plan resolution via
`parallax.openrouter.generate_image`. The smoke fixture in
`tests/fixtures/verify_suite_smoke/basic/` runs at 1080×1920 and
asserts `stages.stills.resolution: 1080x1920`, so the schema branch
itself is exercised — just not in this case.

Tracked in `DEV_LOG.md` as a `[FUTURE]` cleanup. Once the shim
forwards `size`, this case can re-add `stages.stills.resolution:
720x1280`.

## How to run it

From the repo root:

```sh
PARALLAX_TEST_MODE=1 uv run parallax verify-suite tests/integration/res-720x1280/
```

Expect:

```
[PASS] res-720x1280 (Xs)
```

To see the failure renderer, mutate one assertion (e.g. flip
`expected.final.resolution` to `9999x9999`) and re-run — exit code 1,
with a `final.resolution: expected …, got …` line.

## How to author a new case (by hand)

1. Make a new sibling folder: `tests/integration/res-480x854/` (or
   wherever — `verify-suite` accepts any directory of case folders).
2. Copy `plan.yaml`, edit the `resolution:`, scenes, and any caption /
   headline options. Keep `voice`, `model`, and per-scene `prompt`/
   `vo_text` filled — the test-mode mocks honor `resolution:` but
   require the rest of the plan to be well-formed.
3. Copy `expected.yaml`, update `name:`, `final.resolution`, and
   every per-stage `resolution:` to match. Update `final.scene_count`
   if the plan has a different number of scenes.
4. Run `PARALLAX_TEST_MODE=1 uv run parallax verify-suite <new-folder>/`.

## How to author a new case (with the scaffolder)

```sh
parallax verify-init tests/integration/res-480x854/ \
  --from tests/integration/res-720x1280/ \
  --resolution 480x854
```

This copies the case verbatim, then rewrites both `plan.yaml`'s
`resolution:` and `expected.final.resolution` to match. Refuses to
overwrite an existing target unless `--force` is passed.

Without `--from`, `verify-init` writes a minimal starter (one scene,
`final` block only) — useful for cases that don't need the full
schema exercised.

## `--paid` semantics

Cases with `paid: true` in `expected.yaml` are skipped by default.
Pass `--paid` to `verify-suite` to include them — they run against
real APIs and cost real money. This reference case is `paid: false`
(test-mode only); paid reference cases live separately and ship in
Block 2 / Phase 2.2.

## Where other cases live

`tests/integration/` hosts canonical cases the test runner should
keep green forever — committed, deterministic, free.

`parallax-demo/test_*` (sister repo) hosts longer-running and paid
cases authored by operators against real footage. They share the
exact `expected.yaml` schema; the runner doesn't care where the case
folder lives, only that it has both `plan.yaml` and `expected.yaml`.
