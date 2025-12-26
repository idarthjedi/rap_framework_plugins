"""Configuration schema and loader for RAP Importer."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class WatchConfig:
    """Configuration for file watching."""

    base_folder: str
    file_patterns: list[str] = field(default_factory=lambda: ["*.pdf"])
    ignore_patterns: list[str] = field(
        default_factory=lambda: ["*.download", "*.crdownload", "*.tmp"]
    )
    stability_check_seconds: float = 1.0
    stability_timeout_seconds: float = 60.0

    @property
    def expanded_base_folder(self) -> Path:
        """Return base folder with ~ expanded."""
        return Path(os.path.expanduser(self.base_folder))


@dataclass
class ScriptConfig:
    """Configuration for a single script in the pipeline."""

    name: str
    type: str  # "applescript" or "python"
    path: str
    enabled: bool = True
    args: dict[str, str] | list[str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in ("applescript", "python"):
            raise ValueError(f"Invalid script type: {self.type}. Must be 'applescript' or 'python'")


@dataclass
class PipelineConfig:
    """Configuration for the processing pipeline."""

    scripts: list[ScriptConfig]
    retry_count: int = 3
    retry_delay_seconds: float = 5.0
    delete_on_success: bool = True

    @property
    def enabled_scripts(self) -> list[ScriptConfig]:
        """Return only enabled scripts."""
        return [s for s in self.scripts if s.enabled]


@dataclass
class LoggingConfig:
    """Configuration for logging."""

    level: str = "INFO"
    file: str = "~/Library/Logs/rap-importer.log"
    max_bytes: int = 10485760  # 10MB
    backup_count: int = 5

    @property
    def expanded_file(self) -> Path:
        """Return log file path with ~ expanded."""
        return Path(os.path.expanduser(self.file))

    def __post_init__(self) -> None:
        valid_levels = ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        if self.level.upper() not in valid_levels:
            raise ValueError(f"Invalid log level: {self.level}. Must be one of {valid_levels}")


@dataclass
class NotificationsConfig:
    """Configuration for macOS notifications."""

    enabled: bool = True
    on_error: bool = True
    on_success: bool = False


@dataclass
class Config:
    """Root configuration object."""

    watch: WatchConfig
    pipeline: PipelineConfig
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)


def _parse_watch_config(data: dict[str, Any]) -> WatchConfig:
    """Parse watch configuration from dict."""
    return WatchConfig(
        base_folder=data["base_folder"],
        file_patterns=data.get("file_patterns", ["*.pdf"]),
        ignore_patterns=data.get("ignore_patterns", ["*.download", "*.crdownload", "*.tmp"]),
        stability_check_seconds=data.get("stability_check_seconds", 1.0),
        stability_timeout_seconds=data.get("stability_timeout_seconds", 60.0),
    )


def _parse_script_config(data: dict[str, Any]) -> ScriptConfig:
    """Parse script configuration from dict."""
    return ScriptConfig(
        name=data["name"],
        type=data["type"],
        path=data["path"],
        enabled=data.get("enabled", True),
        args=data.get("args", {}),
    )


def _parse_pipeline_config(data: dict[str, Any]) -> PipelineConfig:
    """Parse pipeline configuration from dict."""
    scripts = [_parse_script_config(s) for s in data.get("scripts", [])]
    return PipelineConfig(
        scripts=scripts,
        retry_count=data.get("retry_count", 3),
        retry_delay_seconds=data.get("retry_delay_seconds", 5.0),
        delete_on_success=data.get("delete_on_success", True),
    )


def _parse_logging_config(data: dict[str, Any] | None) -> LoggingConfig:
    """Parse logging configuration from dict."""
    if data is None:
        return LoggingConfig()
    return LoggingConfig(
        level=data.get("level", "INFO"),
        file=data.get("file", "~/Library/Logs/rap-importer.log"),
        max_bytes=data.get("max_bytes", 10485760),
        backup_count=data.get("backup_count", 5),
    )


def _parse_notifications_config(data: dict[str, Any] | None) -> NotificationsConfig:
    """Parse notifications configuration from dict."""
    if data is None:
        return NotificationsConfig()
    return NotificationsConfig(
        enabled=data.get("enabled", True),
        on_error=data.get("on_error", True),
        on_success=data.get("on_success", False),
    )


def load_config(config_path: str | Path) -> Config:
    """Load configuration from a JSON file.

    Args:
        config_path: Path to the config.json file

    Returns:
        Parsed Config object

    Raises:
        FileNotFoundError: If config file doesn't exist
        json.JSONDecodeError: If config file is invalid JSON
        ValueError: If config is missing required fields
    """
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        data = json.load(f)

    # Validate required sections
    if "watch" not in data:
        raise ValueError("Config must have a 'watch' section")
    if "base_folder" not in data["watch"]:
        raise ValueError("Config watch section must have 'base_folder'")
    if "pipeline" not in data:
        raise ValueError("Config must have a 'pipeline' section")

    return Config(
        watch=_parse_watch_config(data["watch"]),
        pipeline=_parse_pipeline_config(data["pipeline"]),
        logging=_parse_logging_config(data.get("logging")),
        notifications=_parse_notifications_config(data.get("notifications")),
    )


def find_config_file(start_path: Path | None = None) -> Path:
    """Find config.json in current directory or parent directories.

    Args:
        start_path: Starting directory (defaults to current working directory)

    Returns:
        Path to config.json

    Raises:
        FileNotFoundError: If no config.json found
    """
    if start_path is None:
        start_path = Path.cwd()

    current = start_path
    while current != current.parent:
        config_path = current / "config.json"
        if config_path.exists():
            return config_path
        current = current.parent

    # Check root too
    config_path = current / "config.json"
    if config_path.exists():
        return config_path

    raise FileNotFoundError("No config.json found in current directory or parents")
