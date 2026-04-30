# basic — verify suite smoke case

Two-scene plan run end-to-end in `PARALLAX_TEST_MODE=1` against the
existing parallax stage mocks (no network, no spend).

`expected.yaml` exercises every schema branch the runner supports:
`final` (resolution, duration min/max, audio-video diff, scene count),
per-stage `files_must_exist` + `resolution` + `contiguous_cover`,
`manifest` keys + scene keys, and `run_log` must/must-not contain.

The `cost_usd_max: 0.0` guardrail catches any accidental real-API leak
from a future stage that bypasses the test-mode mocks.

Run from the repo root:

    PARALLAX_TEST_MODE=1 uv run parallax verify suite tests/fixtures/verify_suite_smoke/

Expect `[PASS] basic`. To see the failure renderer, mutate
`expected.final.resolution` in this folder to `9999x9999` and re-run —
exit code 1, with a `final.resolution: expected …, got …` line.
