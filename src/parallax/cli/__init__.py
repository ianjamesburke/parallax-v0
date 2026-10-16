from __future__ import annotations

import sys
from typing import Optional

import typer

from .. import __version__
from . import _audio, _guide, _image, _log, _meta, _models, _produce, _reference, _schema, _validate, _video


app = typer.Typer(
    name="parallax",
    help="Agentic creative production CLI.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        print(f"parallax {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
    verbose: int = typer.Option(
        0, "--verbose", "-v", count=True,
        help="Increase log verbosity: -v=INFO, -vv=DEBUG.",
    ),
) -> None:
    from ..log import configure as configure_logging
    import logging
    level: int | None = None
    if verbose >= 2:
        level = logging.DEBUG
    elif verbose == 1:
        level = logging.INFO
    configure_logging(level)


# Register sub-typers
app.add_typer(_audio.audio_app, name="audio")
app.add_typer(_video.video_app, name="video")
app.add_typer(_image.image_app, name="image")
app.add_typer(_models.models_app, name="models")
app.add_typer(_reference.reference_app, name="reference")

# Register top-level commands from modules
_produce.register_produce(app)
_log.register_log(app)
_meta.register_meta(app)
_schema.register_schema(app)
_validate.register_validate(app)
_guide.register_guide(app)


def main(argv: list[str] | None = None) -> int:
    try:
        result = app(standalone_mode=False, args=argv)
        return result if isinstance(result, int) else 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0
    except typer.Exit as e:
        return e.exit_code
    except typer.Abort:
        return 130
    except Exception as e:
        # Usage errors are ClickExceptions, which typer >= 0.27 vendors, so match on the
        # shared protocol (`show`, `exit_code`) rather than a click class.
        if hasattr(e, "show") and hasattr(e, "exit_code"):
            e.show(file=sys.stderr)
            # Bare sub-group invocation prints help: success
            return 0 if type(e).__name__ == "NoArgsIsHelpError" else e.exit_code
        import logging
        logging.getLogger(__name__).exception("unhandled error in main")
        print(f"\nError: {type(e).__name__}: {e}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
