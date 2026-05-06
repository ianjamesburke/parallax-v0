from __future__ import annotations

import argparse
import shutil
import subprocess
import sys


def register_parser(sub: argparse._SubParsersAction) -> None:
    usage_p = sub.add_parser("usage", help="Per-model / per-session cost summary.")
    usage_p.add_argument(
        "--include-test", action="store_true",
        help="Include PARALLAX_TEST_MODE records (excluded by default).",
    )

    sub.add_parser("credits", help="OpenRouter balance.")

    sub.add_parser("update", help="Upgrade parallax via uv.")

    completions_p = sub.add_parser("completions", help="Manage shell tab completion.")
    completions_sub = completions_p.add_subparsers(dest="completions_command", required=True)

    completions_install_p = completions_sub.add_parser(
        "install",
        help="Write the completion stub to a cache file and print the line to add to your shell config.",
    )
    completions_install_p.add_argument(
        "--shell", choices=["zsh", "bash"], default=None,
        help="Target shell (default: detect from $SHELL).",
    )
    completions_install_p.add_argument(
        "--path", default=None,
        help="Output path (default: ~/.cache/<shell>/parallax-completion.<shell>).",
    )

    completions_print_p = completions_sub.add_parser(
        "print",
        help="Print the completion stub to stdout (escape hatch — prefer `install`).",
    )
    completions_print_p.add_argument("shell", choices=["zsh", "bash"])

    verify_p = sub.add_parser("verify", help="Run or scaffold verify suite cases.")
    verify_sub = verify_p.add_subparsers(dest="verify_command", required=True)

    verify_suite_p = verify_sub.add_parser(
        "suite",
        help="Run case folders against expected.yaml.",
        description=(
            "Each case subfolder must contain a plan.yaml + expected.yaml. "
            "expected.yaml schema (every block optional): "
            "final.{resolution,duration_s,audio_video_diff_s_max,scene_count}, "
            "stages.<name>.{files_must_exist,resolution,contiguous_cover}, "
            "manifest.{keys_required,scene_keys_required}, "
            "run_log.{must_not_contain,must_contain}, cost_usd_max, paid."
        ),
    )
    verify_suite_p.add_argument("suite_dir", help="Directory containing one or more case subfolders.")
    verify_suite_p.add_argument(
        "--paid", action="store_true",
        help="Run cases marked paid: true (default skips them).",
    )
    verify_suite_p.add_argument(
        "--case", default=None,
        help="Run only a single case subfolder by name (default: all).",
    )

    verify_init_p = verify_sub.add_parser(
        "init",
        help="Scaffold a new verify suite case.",
        description=(
            "Creates a new case folder at <target>. With --from <existing>, "
            "copies that case verbatim and optionally rewrites the resolution. "
            "Without --from, writes a minimal one-scene starter that points "
            "at the canonical reference case for the full schema."
        ),
    )
    verify_init_p.add_argument("target", help="Path to the new case folder.")
    verify_init_p.add_argument(
        "--from", dest="from_dir", default=None,
        help="Copy from an existing case folder (must contain plan.yaml + expected.yaml).",
    )
    verify_init_p.add_argument(
        "--resolution", default=None,
        help="WxH (e.g. 480x854). Rewrites plan.yaml's resolution and expected.final.resolution.",
    )
    verify_init_p.add_argument(
        "--force", action="store_true",
        help="Overwrite the target if it already exists (default: refuse).",
    )


def run(args) -> int:
    if args.command == "usage":
        from .. import usage
        _print_usage(usage.summarize(include_test=args.include_test))
        return 0

    if args.command == "credits":
        from ..openrouter import InsufficientCreditsError, check_credits
        try:
            balance = check_credits(min_balance_usd=0.0)
            print(
                f"OpenRouter credits — total ${balance.total:.2f}, "
                f"used ${balance.used:.2f}, remaining ${balance.remaining:.2f}"
            )
            if balance.remaining < 0.50:
                print("  ⚠ Low. Top up at https://openrouter.ai/settings/credits", file=sys.stderr)
                return 1
            return 0
        except InsufficientCreditsError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"Error: could not fetch credits ({type(e).__name__}: {e})", file=sys.stderr)
            return 1

    if args.command == "update":
        return _run_update()

    if args.command == "completions":
        if args.completions_command == "install":
            return _run_completions_install(args.shell, args.path)
        if args.completions_command == "print":
            return _run_completions_print(args.shell)

    if args.command == "verify":
        if args.verify_command == "suite":
            from ..verify_suite import cli_run
            return cli_run(args.suite_dir, paid=args.paid, case=args.case)
        if args.verify_command == "init":
            from ..verify_suite import cli_init
            return cli_init(
                args.target,
                from_dir=args.from_dir,
                resolution=args.resolution,
                force=args.force,
            )
        return 1

    return 2


def _print_usage(summary: dict) -> None:
    scope = "all records (incl. test mode)" if summary["include_test_mode"] else "real runs only"
    print(f"Usage summary — {scope}")
    print(f"Log: {summary['log_path']}")
    print(
        f"Totals: {summary['total_calls']} calls, "
        f"${summary['total_cost_usd']:.4f}, "
        f"{summary['total_duration_ms']} ms, "
        f"{summary['session_count']} sessions"
    )
    by_alias = summary["by_alias"]
    if not by_alias:
        print("(no records)")
        return
    print(f"{'alias':<14}{'tier':<10}{'calls':>7}{'cost_usd':>12}{'duration_ms':>14}")
    for alias, slot in sorted(by_alias.items()):
        print(
            f"{alias:<14}{slot['tier']:<10}{slot['calls']:>7}"
            f"{slot['cost_usd']:>12.4f}{slot['duration_ms']:>14}"
        )


def _run_update() -> int:
    import pathlib
    import re
    import urllib.request
    from importlib.metadata import version as pkg_version

    uv = shutil.which("uv")
    if not uv:
        print(
            "uv not found on PATH. Install it first:\n"
            "  curl -LsSf https://astral.sh/uv/install.sh | sh",
            file=sys.stderr,
        )
        return 1

    installed = pkg_version("parallax")

    remote: str | None = None
    try:
        url = "https://raw.githubusercontent.com/ianjamesburke/parallax-v0/main/pyproject.toml"
        with urllib.request.urlopen(url, timeout=5) as resp:
            content = resp.read().decode()
        m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if m:
            remote = m.group(1)
    except Exception:
        print("Could not check remote version, upgrading anyway…", file=sys.stderr)

    if remote is not None and installed == remote:
        print(f"Already up to date (v{installed}).")
        return 0

    if remote is not None:
        print(f"Updating parallax v{installed} → v{remote}…")
    else:
        print("Upgrading parallax via uv tool upgrade --reinstall…")

    result = subprocess.run([uv, "tool", "upgrade", "parallax", "--reinstall"])
    if result.returncode != 0:
        return result.returncode

    _update_skill()
    return 0


def _update_skill() -> None:
    import pathlib
    skill_dir = pathlib.Path.home() / ".claude" / "skills" / "parallax"
    if not skill_dir.is_dir():
        return
    git = shutil.which("git")
    if not git:
        return
    print("Updating parallax skill…")
    result = subprocess.run(
        [git, "-C", str(skill_dir), "pull", "--ff-only"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        msg = result.stdout.strip() or result.stderr.strip()
        print(f"  Skill: {msg}")
    else:
        print(f"  Skill update failed: {result.stderr.strip()}", file=sys.stderr)


def _run_completions_print(shell: str) -> int:
    import argcomplete
    print(argcomplete.shellcode(["parallax"], shell=shell))  # type: ignore[attr-defined]
    return 0


def _detect_shell() -> str:
    import os
    name = os.path.basename(os.environ.get("SHELL", ""))
    if name in {"zsh", "bash"}:
        return name
    return "zsh"


def _run_completions_install(shell: str | None, path: str | None) -> int:
    import argcomplete
    from pathlib import Path

    target_shell = shell or _detect_shell()
    if target_shell not in {"zsh", "bash"}:
        print(f"Unsupported shell: {target_shell}", file=sys.stderr)
        return 1

    if path:
        out = Path(path).expanduser()
    else:
        out = Path.home() / ".cache" / target_shell / f"parallax-completion.{target_shell}"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(argcomplete.shellcode(["parallax"], shell=target_shell))  # type: ignore[attr-defined]

    print(f"Wrote {target_shell} completion stub to {out}")
    print()
    print("Add this line to your shell config (e.g. ~/.zshrc or ~/dotfiles/zshrc):")
    print(f"  source {out}")
    print()
    print(f"Then restart your shell. To refresh later: rm {out} && parallax completions install")
    return 0
