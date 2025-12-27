"""Tests for CLI argument parsing."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from rap_importer_plugin.cli import CLIArgs, ExecutionMode, cli, parse_args


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

    def test_foreground_flag(self) -> None:
        """--foreground should set foreground mode (for debugging)."""
        args = parse_args(["--foreground"])
        assert args.mode == ExecutionMode.FOREGROUND

    def test_default_config_path(self) -> None:
        """Default config path should be config/config.json."""
        args = parse_args([])
        assert args.config_path == Path("config/config.json")

    def test_custom_config_path(self) -> None:
        """--config should set custom config path."""
        args = parse_args(["--config", "custom.json"])
        assert args.config_path == Path("custom.json")

    def test_custom_config_path_equals(self) -> None:
        """--config=value syntax should work."""
        args = parse_args(["--config=custom.json"])
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

    def test_log_level_equals_syntax(self) -> None:
        """--log-level=VALUE syntax should work."""
        args = parse_args(["--log-level=DEBUG"])
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

    def test_combined_flags_equals_syntax(self) -> None:
        """Multiple flags with equals syntax should work together."""
        args = parse_args([
            "--runonce",
            "--config=custom.json",
            "--log-level=DEBUG"
        ])
        assert args.mode == ExecutionMode.RUNONCE
        assert args.config_path == Path("custom.json")
        assert args.log_level == "DEBUG"


class TestClickCLI:
    """Tests using Click's CliRunner."""

    def test_help_output(self) -> None:
        """--help should show help text."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "DEVONthink imports" in result.output
        assert "--background" in result.output
        assert "--foreground" in result.output
        assert "--runonce" in result.output
        assert "--config" in result.output
        assert "--log-level" in result.output

    def test_version_output(self) -> None:
        """--version should show version."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_invalid_log_level(self) -> None:
        """Invalid log level should error."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--log-level=INVALID"])
        assert result.exit_code != 0
        assert "Invalid value" in result.output or "INVALID" in result.output
