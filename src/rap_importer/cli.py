"""Command-line interface for RAP Importer."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


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


def parse_args(args: list[str] | None = None) -> CLIArgs:
    """Parse command-line arguments.

    Args:
        args: List of arguments (defaults to sys.argv[1:])

    Returns:
        Parsed CLIArgs object
    """
    parser = argparse.ArgumentParser(
        prog="rap-importer",
        description="File watcher with configurable pipeline for DEVONthink imports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  rap-importer                     Run in background, return to terminal (default)
  rap-importer --foreground        Run in foreground with console output (debugging)
  rap-importer --runonce           Process existing files and exit
  rap-importer --config my.json    Use custom config file
  rap-importer --log-level DEBUG   Enable debug logging
        """,
    )

    # Execution mode (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--background",
        action="store_true",
        dest="background",
        help="Run continuously in background, return control to terminal (default)",
    )
    mode_group.add_argument(
        "--foreground",
        action="store_true",
        dest="foreground",
        help="Run in foreground with console output (for testing/debugging)",
    )
    mode_group.add_argument(
        "--runonce",
        action="store_true",
        dest="runonce",
        help="Process existing files and exit",
    )

    # Configuration
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=Path("config/config.json"),
        dest="config_path",
        metavar="FILE",
        help="Path to config file (default: config/config.json)",
    )

    # Logging
    parser.add_argument(
        "--log-level",
        "-l",
        choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"],
        dest="log_level",
        metavar="LEVEL",
        help="Override log level from config",
    )

    # Version
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version="%(prog)s 0.1.0",
    )

    parsed = parser.parse_args(args)

    # Determine execution mode
    if parsed.runonce:
        mode = ExecutionMode.RUNONCE
    elif parsed.foreground:
        mode = ExecutionMode.FOREGROUND
    else:
        mode = ExecutionMode.BACKGROUND  # Default

    return CLIArgs(
        mode=mode,
        config_path=parsed.config_path,
        log_level=parsed.log_level,
    )
