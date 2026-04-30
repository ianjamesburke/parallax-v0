from __future__ import annotations

import argparse
import logging
import sys

from .. import __version__
from ..log import configure as configure_logging
from . import _audio, _image, _log, _meta, _models, _produce, _video


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parallax", description="Agentic creative production CLI.")
    parser.add_argument("--version", action="version", version=f"parallax {__version__}")
    parser.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="Increase log verbosity: -v=INFO, -vv=DEBUG. Overrides PARALLAX_LOG_LEVEL.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _produce.register_parser(sub)
    _models.register_parser(sub)
    _audio.register_parser(sub)
    _video.register_parser(sub)
    _image.register_parser(sub)
    _log.register_parser(sub)
    _meta.register_parser(sub)

    _enable_help_on_empty(parser)

    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args(argv)

    if getattr(args, "_help_on_empty", None) is not None:
        args._help_on_empty()
        return 0

    level: int | None = None
    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose == 1:
        level = logging.INFO
    configure_logging(level)

    _dispatch = {
        "produce": _produce.run,
        "plan": _produce.run,
        "ingest": _produce.run,
        "models": _models.run,
        "audio": _audio.run,
        "video": _video.run,
        "image": _image.run,
        "log": _log.run,
        "usage": _meta.run,
        "credits": _meta.run,
        "update": _meta.run,
        "completions": _meta.run,
        "verify": _meta.run,
    }
    handler = _dispatch.get(args.command)
    if handler:
        return handler(args)
    return 2


def _enable_help_on_empty(parser: argparse.ArgumentParser) -> None:
    """Walk the parser tree: any parser with subparsers prints its help when
    invoked with no subcommand, instead of erroring. Each ancestor stamps its
    own print_help into args._help_on_empty; the deepest matched parser wins.
    Leaf parsers clear the default so concrete subcommands run normally."""
    has_subparsers = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            has_subparsers = True
            action.required = False
            parser.set_defaults(_help_on_empty=parser.print_help)
            for subparser in action.choices.values():
                _enable_help_on_empty(subparser)
    if not has_subparsers:
        parser.set_defaults(_help_on_empty=None)


if __name__ == "__main__":
    sys.exit(main())
