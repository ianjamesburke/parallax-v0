"""Case-folder verifier — runs `produce` against `plan.yaml`, asserts `expected.yaml`.

A "suite" is a directory of case subfolders. Each case is:
  <case>/
    plan.yaml         # standard parallax plan
    expected.yaml     # assertions against the produced run
    README.md         # optional, ignored
    assets/           # optional pre-locked stills/audio referenced from plan

The runner copies each case folder to a temp dir, invokes `produce.run_plan`
on the copy (so the locked-paths-write-back behaviour doesn't pollute the
fixture), then resolves every asserted path relative to the run's
`out_dir` (the versioned `parallax/output/vN/` dir from `stage_scan`).

`expected.yaml` schema (every block is optional; absent fields skip):

    name: my-case               # display name (defaults to folder name)
    description: ...            # ignored, doc only
    paid: false                 # if true, skipped unless --paid passed
    cost_usd_max: 0.0           # run.usage.total_cost_usd <= this

    final:
      resolution: 1080x1920     # exact w×h match via ffprobe on the final mp4
      duration_s: { min: 5.0, max: 12.0 }
      audio_video_diff_s_max: 0.05
      scene_count: 4            # length of manifest.scenes

    stages:                     # each stage block optional
      stills:
        files_must_exist:
          - "stills/*.png"      # globs resolved under out_dir
        resolution: 1080x1920   # max(w,h) match across matched files
      voiceover:
        files_must_exist: ["audio/voiceover.*", "audio/vo_words.json"]
      assemble:
        files_must_exist: ["video/*ken_burns_draft.mp4"]
        resolution: 1080x1920
        contiguous_cover: true  # manifest scenes start=0, no gaps, cover total
      captions:
        files_must_exist: ["video/*captioned.mp4"]
        resolution: 1080x1920
      headline:
        files_must_exist: ["video/*final.mp4"]
        resolution: 1080x1920

    manifest:
      keys_required: [model, voice, resolution, scenes]
      scene_keys_required: [index, vo_text, prompt, start_s, end_s, duration_s]

    run_log:                    # JSONL run log (<output_dir>/run.log)
      must_not_contain: ["Traceback", "ERROR"]
      must_contain: ["align_scenes", "ken_burns_assemble"]

Each present field becomes one assertion. Each failed assertion appends one
line to `CaseResult.failures`; the runner never raises mid-case so the
operator sees every failure in one pass.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

from .settings import ProductionMode


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass
class CaseResult:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    cost_usd: float = 0.0
    skipped_reason: str | None = None


# --------------------------------------------------------------------------
# Schema validation
# --------------------------------------------------------------------------

_TOP_LEVEL_KEYS = {
    "name", "description", "paid", "cost_usd_max",
    "final", "stages", "manifest", "run_log",
}
_FINAL_KEYS = {"resolution", "duration_s", "audio_video_diff_s_max", "scene_count"}
_STAGE_KEYS = {"files_must_exist", "resolution", "contiguous_cover"}
_MANIFEST_KEYS = {"keys_required", "scene_keys_required"}
_RUN_LOG_KEYS = {"must_not_contain", "must_contain"}


def load_expected(path: Path) -> dict[str, Any]:
    """Parse + structurally validate an `expected.yaml` file.

    Raises ValueError with a precise message on unknown top-level keys or
    malformed nested blocks. We validate up-front so a typo in a fixture
    doesn't silently skip an assertion.
    """
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at top level, got {type(data).__name__}")

    unknown = set(data.keys()) - _TOP_LEVEL_KEYS
    if unknown:
        raise ValueError(f"{path}: unknown top-level keys: {sorted(unknown)}")

    final = data.get("final")
    if final is not None:
        if not isinstance(final, dict):
            raise ValueError(f"{path}: 'final' must be a mapping")
        unknown_final = set(final.keys()) - _FINAL_KEYS
        if unknown_final:
            raise ValueError(f"{path}: unknown keys in 'final': {sorted(unknown_final)}")
        dur = final.get("duration_s")
        if dur is not None and not (isinstance(dur, dict) and {"min", "max"} >= set(dur.keys())):
            raise ValueError(f"{path}: 'final.duration_s' must be a mapping with 'min' and/or 'max'")

    stages = data.get("stages")
    if stages is not None:
        if not isinstance(stages, dict):
            raise ValueError(f"{path}: 'stages' must be a mapping")
        for stage_name, block in stages.items():
            if not isinstance(block, dict):
                raise ValueError(f"{path}: stages.{stage_name} must be a mapping")
            unknown_stage = set(block.keys()) - _STAGE_KEYS
            if unknown_stage:
                raise ValueError(f"{path}: unknown keys in stages.{stage_name}: {sorted(unknown_stage)}")

    manifest = data.get("manifest")
    if manifest is not None:
        if not isinstance(manifest, dict):
            raise ValueError(f"{path}: 'manifest' must be a mapping")
        unknown_man = set(manifest.keys()) - _MANIFEST_KEYS
        if unknown_man:
            raise ValueError(f"{path}: unknown keys in 'manifest': {sorted(unknown_man)}")

    run_log = data.get("run_log")
    if run_log is not None:
        if not isinstance(run_log, dict):
            raise ValueError(f"{path}: 'run_log' must be a mapping")
        unknown_rl = set(run_log.keys()) - _RUN_LOG_KEYS
        if unknown_rl:
            raise ValueError(f"{path}: unknown keys in 'run_log': {sorted(unknown_rl)}")

    return data


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------

def _probe_resolution(path: Path) -> tuple[int, int] | None:
    """Return (width, height) via ffprobe, or None if unprobeable."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )
        w_str, h_str = result.stdout.strip().split(",")
        return int(w_str), int(h_str)
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


def _probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


def _probe_audio_duration(path: Path) -> float | None:
    """Duration of the audio stream specifically (separate from container)."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )
        out = result.stdout.strip()
        return float(out) if out and out != "N/A" else None
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return None


# --------------------------------------------------------------------------
# Assertion helpers — each appends to `failures` on mismatch, never raises.
# --------------------------------------------------------------------------

def _assert_resolution(out_dir: Path, glob_or_path: str, expected: str,
                       label: str, failures: list[str]) -> None:
    matches = sorted(out_dir.glob(glob_or_path)) if any(c in glob_or_path for c in "*?[") \
        else [out_dir / glob_or_path]
    if not matches:
        failures.append(f"{label}.resolution: no files matched {glob_or_path!r} under {out_dir}")
        return
    expected_w, expected_h = (int(x) for x in expected.lower().split("x"))
    for m in matches:
        if not m.exists():
            failures.append(f"{label}.resolution: file does not exist: {m}")
            continue
        probed = _probe_resolution(m)
        if probed is None:
            failures.append(f"{label}.resolution: could not probe {m}")
            continue
        if probed != (expected_w, expected_h):
            failures.append(
                f"{label}.resolution: expected {expected_w}x{expected_h}, "
                f"got {probed[0]}x{probed[1]} ({m.name})"
            )


def _assert_files_exist(out_dir: Path, patterns: list[str], label: str,
                        failures: list[str]) -> None:
    for pat in patterns:
        matches = list(out_dir.glob(pat)) if any(c in pat for c in "*?[") \
            else [out_dir / pat] if (out_dir / pat).exists() else []
        if not matches:
            failures.append(
                f"{label}.files_must_exist: no match for {pat!r} under {out_dir}"
            )


def _assert_contiguous_cover(manifest: dict, total_duration: float | None,
                             failures: list[str]) -> None:
    scenes = manifest.get("scenes") or []
    if not scenes:
        failures.append("assemble.contiguous_cover: manifest has no scenes")
        return
    sorted_scenes = sorted(scenes, key=lambda s: s.get("index", 0))
    if abs(float(sorted_scenes[0].get("start_s", -1.0)) - 0.0) > 0.01:
        failures.append(
            f"assemble.contiguous_cover: scene_0.start_s expected 0.0, "
            f"got {sorted_scenes[0].get('start_s')}"
        )
    for prev, curr in zip(sorted_scenes, sorted_scenes[1:]):
        if abs(float(curr.get("start_s", 0)) - float(prev.get("end_s", 0))) > 0.01:
            failures.append(
                f"assemble.contiguous_cover: scene_{curr.get('index')}.start_s "
                f"({curr.get('start_s')}) does not equal scene_{prev.get('index')}.end_s "
                f"({prev.get('end_s')})"
            )
    if total_duration is not None:
        last_end = float(sorted_scenes[-1].get("end_s", 0.0))
        if abs(last_end - total_duration) > 0.05:
            failures.append(
                f"assemble.contiguous_cover: last scene end_s={last_end:.3f} "
                f"does not match total duration={total_duration:.3f}"
            )


# --------------------------------------------------------------------------
# Main runner
# --------------------------------------------------------------------------

@contextmanager
def _isolated_case(case_dir: Path) -> Iterator[tuple[Path, Path]]:
    """Copy the case folder to a temp dir; yield (copy_dir, plan_path).

    `produce` writes locked asset paths back into the plan YAML, which
    would dirty the fixture across runs. Mirroring lets us run each case
    against a fresh, throwaway copy.
    """
    with tempfile.TemporaryDirectory(prefix="parallax-verify-") as tmp:
        dest = Path(tmp) / case_dir.name
        shutil.copytree(case_dir, dest)
        yield dest, dest / "plan.yaml"


def _read_run_log(run_id: str, out_dir: Path | None) -> str:
    """Read the per-run log from `<output_dir>/run.log`."""
    if out_dir is None:
        return ""
    log_path = out_dir / "run.log"
    if not log_path.exists():
        return ""
    return log_path.read_text(errors="replace")


def run_case(case_dir: Path, paid: bool = False,
             mode: ProductionMode = ProductionMode.TEST) -> CaseResult:
    """Run one case folder; return a `CaseResult` summarizing every assertion."""
    case_name = case_dir.name
    expected_path = case_dir / "expected.yaml"
    plan_path_src = case_dir / "plan.yaml"

    if not plan_path_src.is_file():
        return CaseResult(name=case_name, passed=False,
                          failures=[f"missing plan.yaml in {case_dir}"])
    if not expected_path.is_file():
        return CaseResult(name=case_name, passed=False,
                          failures=[f"missing expected.yaml in {case_dir}"])

    try:
        expected = load_expected(expected_path)
    except (ValueError, yaml.YAMLError) as e:
        return CaseResult(name=case_name, passed=False, failures=[f"schema: {e}"])

    if expected.get("paid") and not paid:
        return CaseResult(name=case_name, passed=True, skipped_reason="paid (use --paid)")

    name = expected.get("name", case_name)
    failures: list[str] = []

    # Mode is plumbed via PARALLAX_TEST_MODE because Settings reads env at
    # resolve_settings() time. We restore the original value after the run.
    prev_test_mode = os.environ.get("PARALLAX_TEST_MODE")
    if mode == ProductionMode.TEST:
        os.environ["PARALLAX_TEST_MODE"] = "1"
    elif "PARALLAX_TEST_MODE" in os.environ:
        del os.environ["PARALLAX_TEST_MODE"]

    t0 = time.monotonic()
    cost_usd = 0.0
    out_dir: Path | None = None
    run_id: str | None = None
    rc = 1
    try:
        with _isolated_case(case_dir) as (case_copy, plan_copy):
            from . import runlog
            from .produce import run_plan

            try:
                rc = run_plan(folder=case_copy, plan_path=plan_copy)
            except Exception as e:  # noqa: BLE001 — collect the failure, don't re-raise
                failures.append(f"produce raised {type(e).__name__}: {e}")

            # Discover the run's output dir — produce snapshots plan.yaml into
            # it, so we look for the highest-numbered v*/ that has a plan.yaml.
            output_base = case_copy / "parallax" / "output"
            if output_base.is_dir():
                versions = sorted(
                    [d for d in output_base.iterdir()
                     if d.is_dir() and d.name.startswith("v") and (d / "plan.yaml").exists()],
                    key=lambda d: int(d.name[1:]) if d.name[1:].isdigit() else 0,
                )
                if versions:
                    out_dir = versions[-1]

            # `run_plan` calls runlog.end_run() before returning, which clears
            # the contextvar — so we read run_id back from cost.json (written
            # by stage_finalize) which is also where the cost guardrail lives.
            cost_json = (out_dir / "cost.json") if out_dir else None
            if cost_json and cost_json.exists():
                try:
                    cost_data = json.loads(cost_json.read_text())
                    cost_usd = float(cost_data.get("cost_usd", 0.0))
                    run_id = cost_data.get("run_id")
                except (json.JSONDecodeError, ValueError, TypeError):
                    pass

            if rc != 0 and not failures:
                failures.append(f"produce returned non-zero exit code: {rc}")

            # Snapshot the run log BEFORE the temp dir is cleaned up.
            run_log_text = _read_run_log(run_id, out_dir) if run_id else ""

            if out_dir is not None:
                _assert_all(expected, out_dir, run_log_text, cost_usd, failures)
            elif rc == 0:
                failures.append("could not locate run output directory under parallax/output/v*/")

    finally:
        if prev_test_mode is None:
            os.environ.pop("PARALLAX_TEST_MODE", None)
        else:
            os.environ["PARALLAX_TEST_MODE"] = prev_test_mode

    duration_s = time.monotonic() - t0
    return CaseResult(
        name=name,
        passed=not failures,
        failures=failures,
        duration_s=duration_s,
        cost_usd=cost_usd,
    )


def _assert_all(expected: dict, out_dir: Path, run_log_text: str,
                cost_usd: float, failures: list[str]) -> None:
    """Run every assertion present in `expected` against the produced run."""
    # cost guardrail
    cost_max = expected.get("cost_usd_max")
    if cost_max is not None:
        if cost_usd > float(cost_max) + 1e-9:
            failures.append(
                f"cost_usd_max: spent ${cost_usd:.4f}, allowed ${float(cost_max):.4f}"
            )

    # manifest — read once, used by both manifest{} and final.scene_count
    manifest_path = out_dir / "manifest.yaml"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = yaml.safe_load(manifest_path.read_text()) or {}
        except yaml.YAMLError as e:
            failures.append(f"manifest: failed to parse manifest.yaml: {e}")

    # final mp4
    final_block = expected.get("final") or {}
    final_path: Path | None = None
    final_mp4s = sorted(p for p in out_dir.glob("*.mp4") if p.is_file())
    if final_mp4s:
        final_path = final_mp4s[-1]

    if final_block:
        if final_path is None:
            failures.append(f"final: no .mp4 found in {out_dir}")
        else:
            res = final_block.get("resolution")
            if res:
                expected_w, expected_h = (int(x) for x in res.lower().split("x"))
                probed = _probe_resolution(final_path)
                if probed is None:
                    failures.append(f"final.resolution: could not probe {final_path}")
                elif probed != (expected_w, expected_h):
                    failures.append(
                        f"final.resolution: expected {expected_w}x{expected_h}, "
                        f"got {probed[0]}x{probed[1]}"
                    )
            duration = _probe_duration(final_path)
            dur_block = final_block.get("duration_s") or {}
            if duration is None and (dur_block or final_block.get("audio_video_diff_s_max") is not None):
                failures.append(f"final.duration_s: could not probe {final_path}")
            else:
                if "min" in dur_block and duration is not None and duration < float(dur_block["min"]) - 1e-3:
                    failures.append(
                        f"final.duration_s.min: expected >= {dur_block['min']}, got {duration:.3f}"
                    )
                if "max" in dur_block and duration is not None and duration > float(dur_block["max"]) + 1e-3:
                    failures.append(
                        f"final.duration_s.max: expected <= {dur_block['max']}, got {duration:.3f}"
                    )
            avd_max = final_block.get("audio_video_diff_s_max")
            if avd_max is not None and duration is not None:
                a_dur = _probe_audio_duration(final_path)
                if a_dur is None:
                    failures.append(f"final.audio_video_diff_s_max: no audio stream in {final_path.name}")
                else:
                    diff = abs(duration - a_dur)
                    if diff > float(avd_max) + 1e-6:
                        failures.append(
                            f"final.audio_video_diff_s_max: video={duration:.3f}s, "
                            f"audio={a_dur:.3f}s, diff={diff:.3f}s > {avd_max}s"
                        )
            sc_count = final_block.get("scene_count")
            if sc_count is not None:
                actual = len(manifest.get("scenes") or [])
                if actual != int(sc_count):
                    failures.append(
                        f"final.scene_count: expected {sc_count}, got {actual} (from manifest)"
                    )

    # stages — file existence + per-stage resolution + contiguous_cover
    stages = expected.get("stages") or {}
    for stage_name, block in stages.items():
        files = block.get("files_must_exist")
        if files:
            _assert_files_exist(out_dir, files, f"stages.{stage_name}", failures)
        res = block.get("resolution")
        if res:
            # If files_must_exist is set, probe its first matching file. Otherwise
            # nothing to probe — just record the unmatched block as a no-op.
            patterns = block.get("files_must_exist") or []
            for pat in patterns:
                _assert_resolution(out_dir, pat, res, f"stages.{stage_name}", failures)
        if block.get("contiguous_cover"):
            total_dur = _probe_duration(final_path) if final_path else None
            _assert_contiguous_cover(manifest, total_dur, failures)

    # manifest contract
    man_block = expected.get("manifest") or {}
    if man_block:
        if not manifest:
            failures.append(f"manifest: missing or unparseable manifest.yaml in {out_dir}")
        else:
            for k in man_block.get("keys_required") or []:
                if k not in manifest:
                    failures.append(f"manifest.keys_required: missing top-level key {k!r}")
            sc_keys = man_block.get("scene_keys_required") or []
            if sc_keys:
                for sc in manifest.get("scenes") or []:
                    missing = [k for k in sc_keys if k not in sc]
                    if missing:
                        failures.append(
                            f"manifest.scene_keys_required: scene "
                            f"index={sc.get('index', '?')} missing {missing}"
                        )

    # run log
    rl = expected.get("run_log") or {}
    if rl:
        if not run_log_text:
            failures.append("run_log: empty or missing run log")
        else:
            for needle in rl.get("must_not_contain") or []:
                if needle in run_log_text:
                    failures.append(f"run_log.must_not_contain: found {needle!r}")
            for needle in rl.get("must_contain") or []:
                if needle not in run_log_text:
                    failures.append(f"run_log.must_contain: missing {needle!r}")


def run_suite(suite_dir: Path, paid: bool = False,
              mode: ProductionMode = ProductionMode.TEST,
              case_filter: str | None = None) -> list[CaseResult]:
    """Run every case subfolder of `suite_dir`, or `suite_dir` itself if it
    is already a case folder (contains both plan.yaml + expected.yaml).

    The single-case shortcut means an operator can point verify suite
    directly at one case (e.g. `verify suite tests/integration/res-720x1280/`)
    without having to wrap it in a parent directory.
    """
    suite_dir = Path(suite_dir).expanduser().resolve()
    if not suite_dir.is_dir():
        raise FileNotFoundError(f"suite directory not found: {suite_dir}")

    # Single-case shortcut: the path itself is a case folder.
    if (suite_dir / "plan.yaml").is_file() and (suite_dir / "expected.yaml").is_file():
        if case_filter and case_filter != suite_dir.name:
            return []
        return [run_case(suite_dir, paid=paid, mode=mode)]

    cases = sorted(
        d for d in suite_dir.iterdir()
        if d.is_dir() and (d / "plan.yaml").is_file() and (d / "expected.yaml").is_file()
    )
    if case_filter:
        cases = [c for c in cases if c.name == case_filter]
    return [run_case(c, paid=paid, mode=mode) for c in cases]


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Scaffolder — `parallax verify init`
# --------------------------------------------------------------------------

_STARTER_PLAN = """\
# Starter plan.yaml — edit scenes, prompts, voice, resolution to taste.
# Runs free in PARALLAX_TEST_MODE=1 against the test-mode mocks.
voice: Kore
voice_speed: 1.0
image_model: mid
video_model: mid
resolution: 1080x1920
captions: skip
scenes:
  - index: 0
    shot_type: broll
    vo_text: A short opening line.
    prompt: Stub still — placeholder content.
"""

_STARTER_EXPECTED = """\
name: {name}
description: Starter case scaffolded by `parallax verify init`.
paid: false
cost_usd_max: 0.0

final:
  resolution: 1080x1920
  scene_count: 1
"""

_STARTER_README = """\
# {name} — verify suite case

Scaffolded by `parallax verify init`. Runs free in
`PARALLAX_TEST_MODE=1` against the test-mode mocks.

Run from the repo root:

    PARALLAX_TEST_MODE=1 uv run parallax verify suite {target_for_readme}

For the full schema reference and a worked example of every assertion
block, see `tests/integration/res-720x1280/README.md`.
"""


def _rewrite_yaml_in_place(path: Path, mutator) -> None:
    """Round-trip a YAML file through `mutator(data)` and write it back.

    `yaml.safe_dump` strips comments — we accept that for now because the
    canonical case's expected.yaml carries the schema documentation, and
    scaffolded cases are starter material the operator owns.
    """
    data = yaml.safe_load(path.read_text()) or {}
    mutator(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def init_case(target_dir: Path, *, from_dir: Path | None = None,
              resolution: str | None = None, force: bool = False) -> None:
    """Scaffold a new case folder at `target_dir`.

    - With `from_dir`: copy that case verbatim, then optionally rewrite
      both `plan.yaml`'s `resolution:` and `expected.final.resolution`
      to `resolution`.
    - Without `from_dir`: write a minimal starter (one scene, `final`
      block only) and a README pointer to the canonical reference case.

    Refuses to overwrite an existing target unless `force=True`.
    Raises FileExistsError / FileNotFoundError / ValueError on bad input.
    """
    target_dir = Path(target_dir).expanduser().resolve()

    if target_dir.exists():
        if not force:
            raise FileExistsError(
                f"target directory already exists: {target_dir} "
                f"(pass --force to overwrite)"
            )
        if target_dir.is_file():
            raise FileExistsError(
                f"target path is a file, not a directory: {target_dir}"
            )
        shutil.rmtree(target_dir)

    if from_dir is not None:
        src = Path(from_dir).expanduser().resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"--from source not found: {src}")
        if not (src / "plan.yaml").is_file() or not (src / "expected.yaml").is_file():
            raise ValueError(
                f"--from source is not a valid case folder "
                f"(missing plan.yaml or expected.yaml): {src}"
            )
        shutil.copytree(src, target_dir)
    else:
        target_dir.mkdir(parents=True)
        (target_dir / "plan.yaml").write_text(_STARTER_PLAN)
        (target_dir / "expected.yaml").write_text(
            _STARTER_EXPECTED.format(name=target_dir.name)
        )
        (target_dir / "README.md").write_text(
            _STARTER_README.format(
                name=target_dir.name,
                target_for_readme=target_dir.name + "/",
            )
        )

    if resolution is not None:
        # Validate format.
        try:
            w, h = (int(x) for x in resolution.lower().split("x"))
            if w <= 0 or h <= 0:
                raise ValueError
        except ValueError as e:
            raise ValueError(
                f"--resolution must be WxH with positive integers, got {resolution!r}"
            ) from e
        plan_path = target_dir / "plan.yaml"
        expected_path = target_dir / "expected.yaml"
        _rewrite_yaml_in_place(plan_path, lambda d: d.__setitem__("resolution", resolution))

        def _rewrite_expected(d: dict) -> None:
            # name: defaults to folder name; only rewrite if present.
            if "name" in d:
                d["name"] = target_dir.name
            final = d.setdefault("final", {})
            final["resolution"] = resolution
            # Per-stage resolution assertions get rewritten too — leaving
            # them stale would silently fail every run until hand-edited.
            for stage_block in (d.get("stages") or {}).values():
                if isinstance(stage_block, dict) and "resolution" in stage_block:
                    stage_block["resolution"] = resolution

        _rewrite_yaml_in_place(expected_path, _rewrite_expected)


def cli_init(target_dir: str | Path, *, from_dir: str | Path | None = None,
             resolution: str | None = None, force: bool = False) -> int:
    """`parallax verify init` implementation. Returns 0 on success."""
    import sys
    try:
        init_case(
            Path(target_dir),
            from_dir=Path(from_dir) if from_dir else None,
            resolution=resolution,
            force=force,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    target = Path(target_dir).expanduser().resolve()
    print(f"Wrote case scaffold → {target}")
    print(f"  Run it: PARALLAX_TEST_MODE=1 uv run parallax verify suite {target}/")
    return 0


def cli_run(suite_dir: str | Path, *, paid: bool = False,
            case: str | None = None) -> int:
    """`parallax verify suite` implementation. Returns 0 if all cases pass."""
    import sys
    suite_path = Path(suite_dir).expanduser().resolve()
    if not suite_path.is_dir():
        print(f"Error: suite directory not found: {suite_path}", file=sys.stderr)
        return 1

    results = run_suite(suite_path, paid=paid, case_filter=case)
    if not results:
        if case:
            print(f"0 cases run (no case named {case!r} with both plan.yaml + expected.yaml)")
        else:
            print("0 cases run")
        return 0

    any_failed = False
    for r in results:
        if r.skipped_reason:
            print(f"[SKIP] {r.name} — {r.skipped_reason}")
            continue
        if r.passed:
            print(f"[PASS] {r.name} ({r.duration_s:.1f}s)")
        else:
            any_failed = True
            print(f"[FAIL] {r.name} ({r.duration_s:.1f}s)")
            for f in r.failures:
                print(f"  — {f}")

    return 1 if any_failed else 0
