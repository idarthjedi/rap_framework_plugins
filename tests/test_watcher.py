"""Tests for file watcher pattern matching."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rap_importer_plugin.config import WatchConfig
from rap_importer_plugin.watcher import StabilityCheckHandler, scan_existing_files


@pytest.fixture
def watch_config() -> WatchConfig:
    """Create a watch config with typical patterns."""
    return WatchConfig(
        base_folder="/tmp/test",
        file_patterns=["*.pdf"],
        ignore_patterns=["*.tmp", "*.download"],
    )


class TestCaseInsensitivePatterns:
    """Tests for case-insensitive file pattern matching."""

    def test_matches_lowercase_extension(self, watch_config: WatchConfig) -> None:
        """Pattern *.pdf should match file.pdf."""
        handler = StabilityCheckHandler(watch_config, MagicMock())
        assert handler._matches_patterns(Path("/tmp/test/document.pdf")) is True

    def test_matches_uppercase_extension(self, watch_config: WatchConfig) -> None:
        """Pattern *.pdf should match file.PDF (case-insensitive)."""
        handler = StabilityCheckHandler(watch_config, MagicMock())
        assert handler._matches_patterns(Path("/tmp/test/document.PDF")) is True

    def test_matches_mixed_case_extension(self, watch_config: WatchConfig) -> None:
        """Pattern *.pdf should match file.Pdf (case-insensitive)."""
        handler = StabilityCheckHandler(watch_config, MagicMock())
        assert handler._matches_patterns(Path("/tmp/test/document.Pdf")) is True

    def test_ignores_uppercase_tmp(self, watch_config: WatchConfig) -> None:
        """Ignore pattern *.tmp should also ignore .TMP (case-insensitive)."""
        handler = StabilityCheckHandler(watch_config, MagicMock())
        assert handler._matches_patterns(Path("/tmp/test/document.TMP")) is False

    def test_ignores_mixed_case_download(self, watch_config: WatchConfig) -> None:
        """Ignore pattern *.download should also ignore .DOWNLOAD."""
        handler = StabilityCheckHandler(watch_config, MagicMock())
        assert handler._matches_patterns(Path("/tmp/test/file.DOWNLOAD")) is False

    def test_no_match_wrong_extension(self, watch_config: WatchConfig) -> None:
        """Files with non-matching extensions should not match."""
        handler = StabilityCheckHandler(watch_config, MagicMock())
        assert handler._matches_patterns(Path("/tmp/test/document.doc")) is False


class TestScanExistingFilesCaseInsensitive:
    """Tests for case-insensitive pattern matching in scan_existing_files."""

    def test_scan_finds_uppercase_pdf(self, tmp_path: Path) -> None:
        """scan_existing_files should find .PDF files with *.pdf pattern."""
        config = WatchConfig(
            base_folder=str(tmp_path),
            file_patterns=["*.pdf"],
            ignore_patterns=[],
        )

        # Create test files with different case extensions
        (tmp_path / "lower.pdf").touch()
        (tmp_path / "upper.PDF").touch()
        (tmp_path / "mixed.Pdf").touch()
        (tmp_path / "other.doc").touch()

        files = scan_existing_files(config)
        filenames = [f.name for f in files]

        assert "lower.pdf" in filenames
        assert "upper.PDF" in filenames
        assert "mixed.Pdf" in filenames
        assert "other.doc" not in filenames
        assert len(files) == 3

    def test_scan_ignores_uppercase_tmp(self, tmp_path: Path) -> None:
        """scan_existing_files should ignore .TMP with *.tmp pattern."""
        config = WatchConfig(
            base_folder=str(tmp_path),
            file_patterns=["*.pdf"],
            ignore_patterns=["*.tmp"],
        )

        # Create test files
        (tmp_path / "document.pdf").touch()
        (tmp_path / "temp.tmp").touch()
        (tmp_path / "temp.TMP").touch()

        files = scan_existing_files(config)
        filenames = [f.name for f in files]

        assert "document.pdf" in filenames
        assert "temp.tmp" not in filenames
        assert "temp.TMP" not in filenames
        assert len(files) == 1
