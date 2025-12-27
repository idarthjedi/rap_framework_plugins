"""Tests for configuration loading."""

from __future__ import annotations

import json
import pytest
from pathlib import Path

from rap_importer_plugin.config import (
    Config,
    WatchConfig,
    PipelineConfig,
    ScriptConfig,
    LoggingConfig,
    NotificationsConfig,
    load_config,
    find_config_file,
)


class TestWatchConfig:
    """Tests for WatchConfig."""

    def test_default_patterns(self) -> None:
        """Default patterns should include PDFs."""
        config = WatchConfig(base_folder="~/test")
        assert config.file_patterns == ["*.pdf"]

    def test_default_ignore_patterns(self) -> None:
        """Default ignore patterns should include download temps."""
        config = WatchConfig(base_folder="~/test")
        assert "*.download" in config.ignore_patterns
        assert "*.crdownload" in config.ignore_patterns

    def test_expanded_base_folder(self) -> None:
        """Base folder should expand ~ to home directory."""
        config = WatchConfig(base_folder="~/Documents/test")
        assert not str(config.expanded_base_folder).startswith("~")
        assert "Documents/test" in str(config.expanded_base_folder)


class TestScriptConfig:
    """Tests for ScriptConfig."""

    def test_valid_applescript_type(self) -> None:
        """AppleScript type should be accepted."""
        config = ScriptConfig(name="test", type="applescript", path="test.scpt")
        assert config.type == "applescript"

    def test_valid_python_type(self) -> None:
        """Python type should be accepted."""
        config = ScriptConfig(name="test", type="python", path="test.py")
        assert config.type == "python"

    def test_invalid_type_raises(self) -> None:
        """Invalid script type should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid script type"):
            ScriptConfig(name="test", type="invalid", path="test.py")

    def test_enabled_by_default(self) -> None:
        """Scripts should be enabled by default."""
        config = ScriptConfig(name="test", type="python", path="test.py")
        assert config.enabled is True

    def test_valid_command_type(self) -> None:
        """Command type should be accepted."""
        config = ScriptConfig(name="test", type="command", path="echo hello")
        assert config.type == "command"

    def test_command_with_cwd(self) -> None:
        """Command type with cwd should be accepted."""
        config = ScriptConfig(
            name="test",
            type="command",
            path="uv run rap test",
            cwd="~/projects/rap"
        )
        assert config.cwd == "~/projects/rap"

    def test_cwd_is_optional(self) -> None:
        """cwd field should default to None."""
        config = ScriptConfig(name="test", type="python", path="test.py")
        assert config.cwd is None

    def test_cwd_works_with_all_types(self) -> None:
        """cwd should be accepted for all script types."""
        for script_type in ("applescript", "python", "command"):
            config = ScriptConfig(
                name="test",
                type=script_type,
                path="test",
                cwd="/some/path"
            )
            assert config.cwd == "/some/path"


class TestPipelineConfig:
    """Tests for PipelineConfig."""

    def test_enabled_scripts(self) -> None:
        """Should filter to only enabled scripts."""
        scripts = [
            ScriptConfig(name="enabled", type="python", path="a.py", enabled=True),
            ScriptConfig(name="disabled", type="python", path="b.py", enabled=False),
            ScriptConfig(name="also_enabled", type="python", path="c.py", enabled=True),
        ]
        config = PipelineConfig(scripts=scripts)

        enabled = config.enabled_scripts
        assert len(enabled) == 2
        assert all(s.enabled for s in enabled)


class TestLoggingConfig:
    """Tests for LoggingConfig."""

    def test_valid_levels(self) -> None:
        """Valid log levels should be accepted."""
        for level in ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = LoggingConfig(level=level)
            assert config.level == level

    def test_invalid_level_raises(self) -> None:
        """Invalid log level should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid log level"):
            LoggingConfig(level="INVALID")

    def test_expanded_file(self) -> None:
        """Log file path should expand ~."""
        config = LoggingConfig(file="~/test.log")
        assert not str(config.expanded_file).startswith("~")


class TestLoadConfig:
    """Tests for loading config from file."""

    def test_load_valid_config(self, tmp_path: Path) -> None:
        """Should load a valid config file."""
        config_data = {
            "watch": {
                "base_folder": "~/test"
            },
            "pipeline": {
                "scripts": [
                    {
                        "name": "Test",
                        "type": "python",
                        "path": "test.py"
                    }
                ]
            }
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = load_config(config_path)
        assert config.watch.base_folder == "~/test"
        assert len(config.pipeline.scripts) == 1

    def test_load_config_with_command_and_cwd(self, tmp_path: Path) -> None:
        """Should load config with command type and cwd field."""
        config_data = {
            "watch": {
                "base_folder": "~/test"
            },
            "pipeline": {
                "scripts": [
                    {
                        "name": "Test Command",
                        "type": "command",
                        "path": "uv run rap test {filename}",
                        "cwd": "~/projects/rap",
                        "args": []
                    }
                ]
            }
        }
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps(config_data))

        config = load_config(config_path)
        script = config.pipeline.scripts[0]
        assert script.type == "command"
        assert script.cwd == "~/projects/rap"
        assert "{filename}" in script.path

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.json")

    def test_missing_watch_section_raises(self, tmp_path: Path) -> None:
        """Should raise ValueError for missing watch section."""
        config_path = tmp_path / "config.json"
        config_path.write_text('{"pipeline": {}}')

        with pytest.raises(ValueError, match="watch"):
            load_config(config_path)

    def test_missing_pipeline_section_raises(self, tmp_path: Path) -> None:
        """Should raise ValueError for missing pipeline section."""
        config_path = tmp_path / "config.json"
        config_path.write_text('{"watch": {"base_folder": "~/test"}}')

        with pytest.raises(ValueError, match="pipeline"):
            load_config(config_path)


class TestFindConfigFile:
    """Tests for finding config file."""

    def test_finds_config_in_current_dir(self, tmp_path: Path) -> None:
        """Should find config/config.json in the given directory."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_path = config_dir / "config.json"
        config_path.write_text('{}')

        found = find_config_file(tmp_path)
        assert found == config_path

    def test_finds_config_in_parent_dir(self, tmp_path: Path) -> None:
        """Should find config/config.json in parent directory."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_path = config_dir / "config.json"
        config_path.write_text('{}')

        found = find_config_file(subdir)
        assert found == config_path

    def test_raises_when_not_found(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError when no config found."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        with pytest.raises(FileNotFoundError):
            find_config_file(subdir)
