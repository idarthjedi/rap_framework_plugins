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
    type: str  # "applescript", "python", or "command"
    path: str  # For command type: the command string; for others: script path
    reqs: str = ""  # Requirements/dependencies description for this script
    enabled: bool = True
    args: dict[str, str] | list[str] = field(default_factory=dict)
    cwd: str | None = None  # Optional working directory (supports ~ expansion)
    include_paths: list[str] = field(default_factory=list)  # fnmatch patterns to include
    exclude_paths: list[str] = field(default_factory=list)  # fnmatch patterns to exclude

    def __post_init__(self) -> None:
        if self.type not in ("applescript", "python", "command"):
            raise ValueError(
                f"Invalid script type: {self.type}. "
                "Must be 'applescript', 'python', or 'command'"
            )


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
class WatcherConfig:
    """Configuration for a single folder watcher with its pipeline."""

    name: str
    watch: WatchConfig
    pipeline: PipelineConfig
    global_exclude_paths: list[str] = field(default_factory=list)  # fnmatch patterns to skip globally
    enabled: bool = True


@dataclass
class Config:
    """Root configuration object."""

    watchers: list[WatcherConfig]
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)

    @property
    def enabled_watchers(self) -> list[WatcherConfig]:
        """Return only enabled watchers."""
        return [w for w in self.watchers if w.enabled]


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
        reqs=data.get("reqs", ""),
        enabled=data.get("enabled", True),
        args=data.get("args", {}),
        cwd=data.get("cwd"),
        include_paths=data.get("include_paths", []),
        exclude_paths=data.get("exclude_paths", []),
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


def _parse_watcher_config(data: dict[str, Any]) -> WatcherConfig:
    """Parse a single watcher configuration from dict.

    Args:
        data: Watcher configuration dict

    Returns:
        Parsed WatcherConfig object

    Raises:
        ValueError: If required fields are missing
    """
    if "name" not in data:
        raise ValueError("Watcher config must have a 'name'")
    if "watch" not in data:
        raise ValueError(f"Watcher '{data['name']}' must have a 'watch' section")
    if "base_folder" not in data["watch"]:
        raise ValueError(f"Watcher '{data['name']}' watch section must have 'base_folder'")
    if "pipeline" not in data:
        raise ValueError(f"Watcher '{data['name']}' must have a 'pipeline' section")

    return WatcherConfig(
        name=data["name"],
        watch=_parse_watch_config(data["watch"]),
        pipeline=_parse_pipeline_config(data["pipeline"]),
        global_exclude_paths=data.get("global_exclude_paths", []),
        enabled=data.get("enabled", True),
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
    if "watchers" not in data:
        raise ValueError("Config must have a 'watchers' array")

    if not isinstance(data["watchers"], list) or len(data["watchers"]) == 0:
        raise ValueError("Config 'watchers' must be a non-empty array")

    watchers = [_parse_watcher_config(w) for w in data["watchers"]]

    return Config(
        watchers=watchers,
        logging=_parse_logging_config(data.get("logging")),
        notifications=_parse_notifications_config(data.get("notifications")),
    )


def find_config_file(start_path: Path | None = None) -> Path:
    """Find config/config.json in current directory or parent directories.

    Args:
        start_path: Starting directory (defaults to current working directory)

    Returns:
        Path to config/config.json

    Raises:
        FileNotFoundError: If no config/config.json found
    """
    if start_path is None:
        start_path = Path.cwd()

    current = start_path
    while current != current.parent:
        config_path = current / "config" / "config.json"
        if config_path.exists():
            return config_path
        current = current.parent

    # Check root too
    config_path = current / "config" / "config.json"
    if config_path.exists():
        return config_path

    raise FileNotFoundError("No config/config.json found in current directory or parents")
