"""Command-line interface for RAP Importer."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ExecutionMode(Enum):
    """Execution mode for the application."""

    BACKGROUND = "background"  # Continuous watching with menu bar
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
  rap-importer                     Run continuously with menu bar (default)
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
        help="Run continuously with menu bar icon (default)",
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
        default=Path("config.json"),
        dest="config_path",
        metavar="FILE",
        help="Path to config file (default: config.json)",
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
    else:
        mode = ExecutionMode.BACKGROUND  # Default

    return CLIArgs(
        mode=mode,
        config_path=parsed.config_path,
        log_level=parsed.log_level,
    )
