"""Shared headless Chrome launch arguments.

Every Parallax Chrome launch (HTML motion rendering, storyboard PDF) builds its
argv here so the sandbox policy lives in one place.

Chrome runs sandboxed by default, including when Parallax runs as root. If the
sandbox genuinely cannot work (typically Chrome as root inside a container),
the operator opts out explicitly with ``PARALLAX_CHROME_NO_SANDBOX=1``. The
opt-out prints a loud warning on every launch. Parallax never adds
``--no-sandbox`` on its own: not after a failed launch and not by detecting root.
"""

from __future__ import annotations

import os
import sys

NO_SANDBOX_ENV = "PARALLAX_CHROME_NO_SANDBOX"
_TRUTHY = {"1", "true", "yes", "on"}

NO_SANDBOX_WARNING = (
    f"WARNING: {NO_SANDBOX_ENV} is set, so headless Chrome is running WITHOUT its sandbox. "
    "Generated or supplied HTML can then exploit a Chrome bug to run code as this user. "
    f"Unset {NO_SANDBOX_ENV} unless Chrome cannot start sandboxed (e.g. as root in a container)."
)


def chrome_sandbox_disabled() -> bool:
    """True only when the operator explicitly opted out of the Chrome sandbox."""
    return os.environ.get(NO_SANDBOX_ENV, "").strip().lower() in _TRUTHY


def chrome_launch_args(chrome_bin: str, *flags: str) -> list[str]:
    """Return ``[chrome_bin, <sandbox flags>, *flags]`` for a headless Chrome launch.

    ``--no-sandbox`` is never accepted via ``flags``; it is only added here,
    and only with the explicit env opt-out, which warns on stderr every call.
    """
    if any(f.strip().lower() == "--no-sandbox" for f in flags):
        raise ValueError(f"pass sandbox policy via {NO_SANDBOX_ENV}, not a --no-sandbox flag")
    args = [str(chrome_bin)]
    if chrome_sandbox_disabled():
        print(NO_SANDBOX_WARNING, file=sys.stderr, flush=True)
        try:
            from . import runlog
            runlog.event("chrome.no_sandbox", level="WARN", env=NO_SANDBOX_ENV)
        except Exception:
            pass
        args.append("--no-sandbox")
    args.extend(flags)
    return args


SANDBOX_HINT = (
    f"If Chrome cannot start sandboxed (e.g. running as root in a container), set "
    f"{NO_SANDBOX_ENV}=1 to opt out explicitly (Parallax will warn on every launch)."
)
