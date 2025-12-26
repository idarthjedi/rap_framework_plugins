"""Tests for CLI argument parsing."""

from __future__ import annotations

from pathlib import Path

from rap_importer.cli import CLIArgs, ExecutionMode, parse_args


class TestParseArgs:
    """Tests for parse_args function."""

    def test_default_mode_is_background(self) -> None:
        """Default mode should be background."""
        args = parse_args([])
        assert args.mode == ExecutionMode.BACKGROUND

    def test_background_flag(self) -> None:
        """--background should set background mode."""
        args = parse_args(["--background"])
        assert args.mode == ExecutionMode.BACKGROUND

    def test_runonce_flag(self) -> None:
        """--runonce should set runonce mode."""
        args = parse_args(["--runonce"])
        assert args.mode == ExecutionMode.RUNONCE

    def test_default_config_path(self) -> None:
        """Default config path should be config/config.json."""
        args = parse_args([])
        assert args.config_path == Path("config/config.json")

    def test_custom_config_path(self) -> None:
        """--config should set custom config path."""
        args = parse_args(["--config", "custom.json"])
        assert args.config_path == Path("custom.json")

    def test_short_config_flag(self) -> None:
        """-c should work as shorthand for --config."""
        args = parse_args(["-c", "custom.json"])
        assert args.config_path == Path("custom.json")

    def test_default_log_level_is_none(self) -> None:
        """Default log level should be None (use config)."""
        args = parse_args([])
        assert args.log_level is None

    def test_log_level_override(self) -> None:
        """--log-level should override log level."""
        args = parse_args(["--log-level", "DEBUG"])
        assert args.log_level == "DEBUG"

    def test_short_log_level_flag(self) -> None:
        """-l should work as shorthand for --log-level."""
        args = parse_args(["-l", "TRACE"])
        assert args.log_level == "TRACE"

    def test_combined_flags(self) -> None:
        """Multiple flags should work together."""
        args = parse_args([
            "--runonce",
            "--config", "custom.json",
            "--log-level", "DEBUG"
        ])
        assert args.mode == ExecutionMode.RUNONCE
        assert args.config_path == Path("custom.json")
        assert args.log_level == "DEBUG"
