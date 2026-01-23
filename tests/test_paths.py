"""Tests for path expansion utilities."""

import os
from pathlib import Path
from unittest import mock

import pytest

from rap_importer_plugin.paths import expand_path, expand_path_to_path


class TestExpandPath:
    """Tests for expand_path function."""

    def test_expands_tilde(self):
        """Test that ~ is expanded to home directory."""
        result = expand_path("~/documents")
        assert result.startswith(str(Path.home()))
        assert result.endswith("/documents")

    def test_expands_env_var_dollar_brace(self):
        """Test that ${VAR} is expanded."""
        with mock.patch.dict(os.environ, {"TEST_VAR": "/test/path"}):
            result = expand_path("${TEST_VAR}/subdir")
            assert result == "/test/path/subdir"

    def test_expands_env_var_dollar_only(self):
        """Test that $VAR is also expanded."""
        with mock.patch.dict(os.environ, {"TEST_VAR": "/test/path"}):
            result = expand_path("$TEST_VAR/subdir")
            assert result == "/test/path/subdir"

    def test_expands_both_tilde_and_env_var(self):
        """Test that both ~ and ${VAR} work together."""
        with mock.patch.dict(os.environ, {"SUBDIR": "my_folder"}):
            result = expand_path("~/${SUBDIR}/file.txt")
            assert result.startswith(str(Path.home()))
            assert "my_folder" in result

    def test_leaves_undefined_var_unchanged(self):
        """Test that undefined env vars are left as-is for validation to catch."""
        # Ensure the var is not defined
        env = os.environ.copy()
        env.pop("UNDEFINED_VAR", None)
        with mock.patch.dict(os.environ, env, clear=True):
            result = expand_path("${UNDEFINED_VAR}/path")
            # expandvars leaves ${UNDEFINED_VAR} unchanged when not set
            assert "${UNDEFINED_VAR}" in result or result.startswith("/path")

    def test_absolute_path_unchanged(self):
        """Test that absolute paths without vars are unchanged."""
        result = expand_path("/absolute/path/to/file")
        assert result == "/absolute/path/to/file"

    def test_relative_path_unchanged(self):
        """Test that simple relative paths are unchanged."""
        result = expand_path("relative/path")
        assert result == "relative/path"


class TestExpandPathToPath:
    """Tests for expand_path_to_path function."""

    def test_returns_path_object(self):
        """Test that result is a Path object."""
        result = expand_path_to_path("~/documents")
        assert isinstance(result, Path)

    def test_expands_tilde(self):
        """Test that ~ is expanded in Path result."""
        result = expand_path_to_path("~/documents")
        assert result.parts[0] != "~"
        assert str(result).startswith(str(Path.home()))

    def test_expands_env_var(self):
        """Test that env vars are expanded in Path result."""
        with mock.patch.dict(os.environ, {"TEST_BASE": "/test"}):
            result = expand_path_to_path("${TEST_BASE}/subdir")
            assert result == Path("/test/subdir")
