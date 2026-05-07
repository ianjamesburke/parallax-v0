from __future__ import annotations

import sys
from typing import Optional

import typer

from .. import __version__
from . import _audio, _image, _log, _meta, _models, _produce, _schema, _validate, _video


app = typer.Typer(
    name="parallax",
    help="Agentic creative production CLI.",
    no_args_is_help=False,
    invoke_without_command=True,
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

# Register top-level commands from modules
_produce.register_produce(app)
_log.register_log(app)
_meta.register_meta(app)
_schema.register_schema(app)
_validate.register_validate(app)


def main(argv: list[str] | None = None) -> int:
    import click
    try:
        result = app(standalone_mode=False, args=argv)
        return result if isinstance(result, int) else 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0
    except click.exceptions.Exit as e:
        code = getattr(e, "code", 0)
        return int(code) if code is not None else 0
    except click.exceptions.NoArgsIsHelpError:
        # Bare sub-group invocation prints help — treat as success
        return 0
    except click.UsageError as e:
        e.show(file=sys.stderr)
        return 2
    except click.Abort:
        return 130
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("unhandled error in main")
        print(f"\nError: {type(e).__name__}: {e}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
