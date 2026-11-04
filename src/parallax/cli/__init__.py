from __future__ import annotations

import sys
from typing import Optional

import typer

from .. import __version__
from . import _audio, _cut, _export, _guide, _image, _log, _meta, _models, _produce, _reference, _schema, _validate, _video


app = typer.Typer(
    name="parallax",
    help=("Agentic creative production CLI.\n\n"
          "For supplied footage, follow brief → assets import → check → plan → produce (see `parallax guide footage_job`). `parallax cut` is a quick loose-clips tool.\n\n"
          "Agents: start with `parallax guide` (workflow map + topics). "
          "Single shot with start/end frames: `parallax guide single_shot`. "
          "Character design first: `parallax guide character_first`."),
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
app.add_typer(_export.export_app, name="export")

assets_app = typer.Typer(help="Import externally generated media.")

@assets_app.command("import")
def import_assets(folder: str = typer.Option(..., "--folder"), source: str = typer.Option(..., "--from")) -> int:
    import json
    from ..front_half import import_media
    try:
        print(json.dumps(import_media(folder, source), indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        raise typer.Exit(code=1)

app.add_typer(assets_app, name="assets")

@app.command("check")
def check_project(folder: str = typer.Option(..., "--folder"), stage: str = typer.Option("draft", "--stage"), as_json: bool = typer.Option(False, "--json")) -> None:
    """Check a project offline without changing its files."""
    import json
    from ..front_half import check_folder
    if stage not in ("draft", "review", "media"):
        typer.echo("Error: --stage must be draft, review, or media", err=True)
        raise typer.Exit(code=2)
    try:
        result = check_folder(folder, stage)
    except (OSError, ValueError, TypeError) as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc), "exit_code": 1}) if as_json else f"Error reading project: {exc}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(result, indent=2))
    else:
        for f in result["findings"]:
            typer.echo(f"{f['severity'].upper()} [{f['id']}]: {f['message']}\n  Fix: {f['fix']}")
        typer.echo(f"Project check: {result['status']}.")
    if result["exit_code"]: raise typer.Exit(code=result["exit_code"])


# Register top-level commands from modules
_produce.register_produce(app)
_cut.register_cut(app)
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
            print("Agents: start with `parallax guide`; for one move use `parallax guide single_shot`.", file=sys.stderr)
            # Bare sub-group invocation prints help: success
            return 0 if type(e).__name__ == "NoArgsIsHelpError" else e.exit_code
        import logging
        logging.getLogger(__name__).exception("unhandled error in main")
        print(f"\nError: {type(e).__name__}: {e}\n", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
