"""Command-line interface for RAP Importer."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import click


class ExecutionMode(Enum):
    """Execution mode for the application."""

    BACKGROUND = "background"  # Spawn daemon and exit (default)
    FOREGROUND = "foreground"  # Run in foreground with menu bar (used by daemon)
    RUNONCE = "runonce"  # Process existing files and exit


@dataclass
class CLIArgs:
    """Parsed command-line arguments."""

    mode: ExecutionMode
    config_path: Path
    log_level: str | None


@click.command()
@click.option(
    "--background",
    "mode",
    flag_value="background",
    default=True,
    help="Run in background, return control to terminal (default)",
)
@click.option(
    "--foreground",
    "mode",
    flag_value="foreground",
    help="Run in foreground with console output (for debugging)",
)
@click.option(
    "--runonce",
    "mode",
    flag_value="runonce",
    help="Process existing files and exit",
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=False, path_type=Path),
    default=Path("config/config.json"),
    help="Path to config file (default: config/config.json)",
)
@click.option(
    "--log-level",
    "-l",
    "log_level",
    type=click.Choice(["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=True),
    default=None,
    help="Override log level from config",
)
@click.version_option(version="0.1.0", prog_name="rap-importer")
@click.pass_context
def cli(ctx: click.Context, mode: str, config_path: Path, log_level: str | None) -> None:
    """File watcher with configurable pipeline for DEVONthink imports.

    Examples:

    \b
      rap-importer                     Run in background (default)
      rap-importer --foreground        Run in foreground with console output
      rap-importer --runonce           Process existing files and exit
      rap-importer --config=my.json    Use custom config file
      rap-importer --log-level=DEBUG   Enable debug logging
    """
    ctx.obj = CLIArgs(
        mode=ExecutionMode(mode),
        config_path=config_path,
        log_level=log_level,
    )


def parse_args(args: list[str] | None = None) -> CLIArgs:
    """Parse command-line arguments.

    This is a compatibility wrapper that invokes the Click CLI
    and returns the parsed CLIArgs object.

    Args:
        args: List of arguments (defaults to sys.argv[1:])

    Returns:
        Parsed CLIArgs object

    Raises:
        SystemExit: For --help and --version (exits with code 0)
    """
    if args is None:
        args = sys.argv[1:]
    try:
        with cli.make_context("rap-importer", args) as ctx:
            # Invoke the command to populate ctx.obj
            cli.invoke(ctx)
            return ctx.obj
    except click.exceptions.Exit as e:
        # Handle --help and --version which exit cleanly
        sys.exit(e.exit_code)
