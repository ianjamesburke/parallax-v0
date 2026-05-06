from __future__ import annotations

import argparse
import sys


def register_parser(sub: argparse._SubParsersAction) -> None:
    log_p = sub.add_parser(
        "log",
        help="Inspect run logs — summary view by default, or `log list` for all runs.",
        description=(
            "parallax log <spec>            view one run (spec: latest | <short> | <run_id>)\n"
            "parallax log list              tabulate recent runs from the index"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    log_p.add_argument(
        "spec", nargs="?", default="latest",
        help="Run spec: 'latest' (default), 6-hex short id, full run_id, or 'list'.",
    )
    log_p.add_argument(
        "--level", choices=("info", "debug"), default="info",
        help="Minimum level to include (default: info — drops DEBUG events).",
    )
    log_p.add_argument(
        "--summary", dest="summary", action="store_true", default=True,
        help="Operator-readable digest (default).",
    )
    log_p.add_argument(
        "--no-summary", dest="summary", action="store_false",
        help="Emit raw NDJSON (level-filtered).",
    )
    log_p.add_argument(
        "--follow", "-f", action="store_true",
        help="Stream new events live (forces --no-summary).",
    )
    log_p.add_argument("--limit", type=int, default=20, help="`log list` row cap (default: 20).")
    log_p.add_argument(
        "--since", default=None,
        help="`log list` time filter, e.g. '1d', '6h', '30m'.",
    )


def run(args) -> int:
    from pathlib import Path
    from .. import runlog

    if args.spec == "list":
        return _print_log_list(limit=args.limit, since=args.since)

    spec = args.spec or "latest"
    row = runlog.find_run(spec)
    if row is None:
        print(f"Error: no run found for spec {spec!r}", file=sys.stderr)
        return 1

    if args.follow:
        return runlog.tail(spec, follow=True)

    out_dir = row.get("output_dir")
    if not out_dir:
        print(f"Error: run {row.get('run_id')} has no output_dir", file=sys.stderr)
        return 1
    log_path = Path(out_dir) / "run.log"
    if not log_path.exists():
        print(f"Error: log file missing: {log_path}", file=sys.stderr)
        return 1

    events = _load_events(log_path)
    if args.summary:
        _print_log_summary(row, events, level=args.level)
        return 0
    return _print_log_raw(events, level=args.level)


def _load_events(path) -> list[dict]:
    import json as _json
    out: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue
    return out


_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def _level_passes(rec_level: str, min_level: str) -> bool:
    floor = _LEVEL_ORDER.get(min_level.upper(), 20)
    return _LEVEL_ORDER.get(rec_level.upper(), 20) >= floor


def _print_log_raw(events: list[dict], level: str) -> int:
    import json as _json
    for ev in events:
        if not _level_passes(ev.get("level", "INFO"), level):
            continue
        print(_json.dumps(ev, ensure_ascii=False))
    return 0


def _print_log_summary(row: dict, events: list[dict], level: str = "info") -> None:
    from datetime import datetime as _dt
    from pathlib import Path
    rid = row.get("run_id", "?")
    short = row.get("short", rid[-6:])
    started_iso = row.get("started", "")
    ended_iso = row.get("ended", "")

    def _fmt_clock(iso: str) -> str:
        try:
            return _dt.fromisoformat(iso.replace("Z", "+00:00")).strftime("%H:%M:%S")
        except Exception:
            return "?"

    started_clk = _fmt_clock(started_iso)
    ended_clk = _fmt_clock(ended_iso)

    duration_s = 0.0
    try:
        a = _dt.fromisoformat(started_iso.replace("Z", "+00:00"))
        b = _dt.fromisoformat(ended_iso.replace("Z", "+00:00"))
        duration_s = (b - a).total_seconds()
    except Exception:
        pass
    mins = int(duration_s // 60)
    secs = duration_s - mins * 60
    dur_str = f"{mins}m{secs:.0f}s" if mins else f"{secs:.1f}s"

    print(f"run {rid}   short {short}   started {started_clk}  ended {ended_clk}  ({dur_str})")
    plan_path = row.get("plan_path") or "?"
    print(f"plan         {plan_path}")

    final_video = ""
    for ev in events:
        if ev.get("event") == "run.end":
            final_video = ev.get("final_video", "")
    extras = ""
    if final_video and Path(final_video).exists():
        try:
            import subprocess as _sp
            probe = _sp.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,duration",
                 "-of", "csv=p=0", str(final_video)],
                capture_output=True, text=True,
            )
            parts = probe.stdout.strip().split(",")
            if len(parts) >= 3:
                w, h, d = parts[0], parts[1], parts[2]
                extras = f"  ({w}x{h}, {float(d):.2f}s)"
        except Exception:
            pass
    print(f"output       {final_video}{extras}")
    print(f"total cost   ${float(row.get('cost_usd', 0.0)):.2f}")

    stage_durations: list[tuple[str, int]] = []
    for ev in events:
        name = ev.get("event", "")
        if name.startswith("stage.") and name.endswith(".end"):
            stage_name = name[len("stage."):-len(".end")]
            stage_durations.append((stage_name, int(ev.get("duration_ms", 0))))
    if stage_durations:
        print("\nStages")
        for name, dur_ms in stage_durations:
            human = _format_duration_ms(dur_ms)
            print(f"  {name:<11} {human}")

    try:
        from .. import usage as _usage
        records = [r for r in _usage.load_records(include_test=True)
                   if r.get("run_id") == rid]
    except Exception:
        records = []
    if records:
        print("\nProvider calls")
        for r in records:
            backend = r.get("backend", "?")
            alias = r.get("alias", "?")
            model_id = r.get("fal_id", "?")
            dur_ms = int(r.get("duration_ms", 0))
            human = _format_duration_ms(dur_ms)
            print(f"  {backend:<8} {alias:<10} {model_id:<32} {human:>8}   ok")

    warns = [ev for ev in events
             if ev.get("level") in ("WARN", "ERROR") or ev.get("event", "").endswith(".error")]
    if warns:
        print("\nWarnings/errors")
        for ev in warns:
            print(f"  [{ev.get('level', '?')}] {ev.get('event', '?')}: "
                  f"{ev.get('msg') or ev.get('error') or ''}")
    else:
        print("\nNo warnings or errors.")

    if level == "debug":
        debug_events = [ev for ev in events if ev.get("level") == "DEBUG"]
        if debug_events:
            print(f"\nDebug events ({len(debug_events)})")
            import json as _json
            for ev in debug_events:
                print("  " + _json.dumps(ev, ensure_ascii=False))


def _format_duration_ms(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    s = ms / 1000.0
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    rs = s - m * 60
    return f"{m}m{rs:.1f}s"


def _print_log_list(limit: int, since: str | None) -> int:
    from datetime import datetime as _dt, timezone as _tz
    from .. import runlog
    rows = runlog.load_run_index()
    if not rows:
        print("(no runs)")
        return 0
    rows = list(reversed(rows))

    if since:
        delta = _parse_duration(since)
        if delta is None:
            print(f"Error: invalid --since value {since!r} (e.g. '1d', '6h', '30m')", file=sys.stderr)
            return 1
        cutoff = _dt.now(_tz.utc) - delta
        rows = [
            r for r in rows
            if (_ts := _safe_iso(r.get("started"))) is not None and _ts >= cutoff
        ]

    rows = rows[:limit]
    print(f"{'short':<8} {'started':<20} {'status':<8} {'cost':>8}  output")
    print(f"{'-'*8} {'-'*20} {'-'*8} {'-'*8}  {'-'*40}")
    for r in rows:
        started = (r.get("started") or "")[:19].replace("T", " ")
        status = r.get("status", "?")
        cost = float(r.get("cost_usd", 0.0))
        out_dir = r.get("output_dir") or ""
        print(f"{r.get('short', '?'):<8} {started:<20} {status:<8} ${cost:>6.2f}  {out_dir}")
    return 0


def _safe_iso(s):
    from datetime import datetime as _dt
    if not s:
        return None
    try:
        return _dt.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _parse_duration(s: str):
    from datetime import timedelta
    if not s or len(s) < 2:
        return None
    unit = s[-1].lower()
    try:
        n = int(s[:-1])
    except ValueError:
        return None
    if unit == "s":
        return timedelta(seconds=n)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    if unit == "d":
        return timedelta(days=n)
    return None
