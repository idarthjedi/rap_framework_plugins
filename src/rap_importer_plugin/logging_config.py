"""Logging configuration for RAP Importer."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LoggingConfig

# Custom TRACE level (more verbose than DEBUG)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def trace(self: logging.Logger, message: str, *args: object, **kwargs: object) -> None:
    """Log a message at TRACE level."""
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)


# Add trace method to Logger class
logging.Logger.trace = trace  # type: ignore[attr-defined]


class ColoredFormatter(logging.Formatter):
    """Formatter that adds colors to console output."""

    COLORS = {
        "TRACE": "\033[90m",  # Gray
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record with colors."""
        color = self.COLORS.get(record.levelname, "")
        message = super().format(record)
        if color:
            return f"{color}{message}{self.RESET}"
        return message


def setup_logging(
    config: LoggingConfig,
    level_override: str | None = None,
) -> logging.Logger:
    """Configure logging with file rotation and optional console output.

    Console output is automatically enabled when stderr is a TTY (interactive
    terminal) and disabled when running as a daemon (stderr redirected).

    Args:
        config: Logging configuration
        level_override: Optional level to override config (from CLI)

    Returns:
        The root logger for the application
    """
    # Determine log level
    level_name = level_override or config.level
    if level_name.upper() == "TRACE":
        level = TRACE
    else:
        level = getattr(logging, level_name.upper())

    # Get the package logger
    logger = logging.getLogger("rap_importer_plugin")
    logger.setLevel(level)
    logger.handlers.clear()

    # Create log file directory if needed
    log_path = config.expanded_file
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=config.max_bytes,
        backupCount=config.backup_count,
    )
    file_handler.setLevel(level)
    file_formatter = logging.Formatter(
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler with colors (only if stderr is a TTY)
    # This auto-disables console output when running as a daemon
    if sys.stderr.isatty():
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_formatter = ColoredFormatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific module.

    Args:
        name: Module name (will be prefixed with 'rap_importer.')

    Returns:
        Logger instance
    """
    if name.startswith("rap_importer_plugin."):
        return logging.getLogger(name)
    return logging.getLogger(f"rap_importer_plugin.{name}")
