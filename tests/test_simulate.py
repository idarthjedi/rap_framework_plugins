"""Tests for path filter simulation."""

from __future__ import annotations

import pytest

from rap_importer_plugin.config import (
    PipelineConfig,
    ScriptConfig,
    WatchConfig,
    WatcherConfig,
)
from rap_importer_plugin.simulate import (
    FilterResult,
    _check_script_filters,
    _pattern_to_example,
    evaluate_path,
    generate_test_paths,
)


class TestPatternToExample:
    """Tests for _pattern_to_example conversion."""

    def test_leading_wildcard(self) -> None:
        """Leading */ should be replaced with default database."""
        result = _pattern_to_example("*/EndNote/*")
        assert result == "SampleDB/EndNote/test.pdf"

    def test_trailing_wildcard(self) -> None:
        """Trailing /* should be replaced with /test.pdf."""
        result = _pattern_to_example("Liberty.University/*")
        assert result == "Liberty.University/test.pdf"

    def test_explicit_database(self) -> None:
        """Explicit database should be preserved."""
        result = _pattern_to_example("Liberty.University/Harvard Business Review/*")
        assert result == "Liberty.University/Harvard Business Review/test.pdf"

    def test_middle_wildcard(self) -> None:
        """Middle * should be replaced with Sample."""
        result = _pattern_to_example("Database/*/SubFolder/*")
        assert result == "Database/Sample/SubFolder/test.pdf"

    def test_custom_default_database(self) -> None:
        """Custom default database should be used."""
        result = _pattern_to_example("*/Group/*", default_database="MyDB")
        assert result == "MyDB/Group/test.pdf"


class TestCheckScriptFilters:
    """Tests for _check_script_filters function."""

    def test_no_filters_runs(self) -> None:
        """Script with no filters should run."""
        script = ScriptConfig(name="Test", type="python", path="test.py")
        result = _check_script_filters(script, "any/path/file.pdf")
        assert result == FilterResult.RUN

    def test_include_pattern_matches(self) -> None:
        """Script should run when include pattern matches."""
        script = ScriptConfig(
            name="Test",
            type="python",
            path="test.py",
            include_paths=["*/BUSI*/*"],
        )
        result = _check_script_filters(script, "DB/BUSI770/file.pdf")
        assert result == FilterResult.RUN

    def test_include_pattern_no_match(self) -> None:
        """Script should be skipped when include pattern doesn't match."""
        script = ScriptConfig(
            name="Test",
            type="python",
            path="test.py",
            include_paths=["*/BUSI*/*"],
        )
        result = _check_script_filters(script, "DB/CHEM101/file.pdf")
        assert result == FilterResult.SKIPPED

    def test_exclude_pattern_matches(self) -> None:
        """Script should be excluded when exclude pattern matches."""
        script = ScriptConfig(
            name="Test",
            type="python",
            path="test.py",
            exclude_paths=["*/Archive/*"],
        )
        result = _check_script_filters(script, "DB/Archive/file.pdf")
        assert result == FilterResult.EXCLUDED

    def test_exclude_takes_precedence(self) -> None:
        """Exclude should take precedence over include."""
        script = ScriptConfig(
            name="Test",
            type="python",
            path="test.py",
            include_paths=["*/BUSI*/*"],
            exclude_paths=["*/Drafts/*"],
        )
        result = _check_script_filters(script, "DB/BUSI770/Drafts/file.pdf")
        assert result == FilterResult.EXCLUDED

    def test_only_exclude_allows_others(self) -> None:
        """Script with only excludes should run on non-excluded paths."""
        script = ScriptConfig(
            name="Test",
            type="python",
            path="test.py",
            exclude_paths=["*/Archive/*"],
        )
        result = _check_script_filters(script, "DB/Active/file.pdf")
        assert result == FilterResult.RUN


class TestEvaluatePath:
    """Tests for evaluate_path function."""

    @pytest.fixture
    def basic_watcher(self) -> WatcherConfig:
        """Create a basic watcher config for testing."""
        return WatcherConfig(
            name="Test Watcher",
            watch=WatchConfig(base_folder="~/test"),
            pipeline=PipelineConfig(
                scripts=[
                    ScriptConfig(
                        name="Script A",
                        type="python",
                        path="a.py",
                        include_paths=["*/Course/*"],
                    ),
                    ScriptConfig(
                        name="Script B",
                        type="python",
                        path="b.py",
                        exclude_paths=["*/Archive/*"],
                    ),
                ]
            ),
            global_exclude_paths=["*/EndNote/*"],
        )

    def test_global_exclude_blocks_all(self, basic_watcher: WatcherConfig) -> None:
        """Globally excluded paths should skip all scripts."""
        result = evaluate_path("DB/EndNote/file.pdf", basic_watcher)
        assert result.globally_excluded is True
        assert result.script_results == {}

    def test_not_globally_excluded(self, basic_watcher: WatcherConfig) -> None:
        """Non-excluded paths should evaluate script filters."""
        result = evaluate_path("DB/Course/file.pdf", basic_watcher)
        assert result.globally_excluded is False
        assert len(result.script_results) == 2

    def test_script_results_populated(self, basic_watcher: WatcherConfig) -> None:
        """Script results should contain status for each script."""
        result = evaluate_path("DB/Course/file.pdf", basic_watcher)
        assert "Script A" in result.script_results
        assert "Script B" in result.script_results
        assert result.script_results["Script A"] == FilterResult.RUN
        assert result.script_results["Script B"] == FilterResult.RUN

    def test_mixed_script_results(self, basic_watcher: WatcherConfig) -> None:
        """Different scripts can have different results for same path."""
        result = evaluate_path("DB/Other/file.pdf", basic_watcher)
        # Script A requires */Course/*, so this should be SKIPPED
        assert result.script_results["Script A"] == FilterResult.SKIPPED
        # Script B only has exclude, so this should RUN
        assert result.script_results["Script B"] == FilterResult.RUN


class TestGenerateTestPaths:
    """Tests for generate_test_paths function."""

    @pytest.fixture
    def watcher_with_patterns(self) -> WatcherConfig:
        """Create a watcher with various patterns."""
        return WatcherConfig(
            name="Test",
            watch=WatchConfig(base_folder="~/test"),
            pipeline=PipelineConfig(
                scripts=[
                    ScriptConfig(
                        name="Script 1",
                        type="python",
                        path="s1.py",
                        include_paths=["Liberty University/*"],
                    ),
                    ScriptConfig(
                        name="Script 2",
                        type="python",
                        path="s2.py",
                        include_paths=["*/Harvard Business Review/*"],
                        exclude_paths=["*/Archive/*"],
                    ),
                ]
            ),
            global_exclude_paths=["*/EndNote/*"],
        )

    def test_generates_from_global_excludes(self, watcher_with_patterns: WatcherConfig) -> None:
        """Should generate paths from global exclude patterns."""
        paths = generate_test_paths(watcher_with_patterns)
        assert any("EndNote" in p for p in paths)

    def test_generates_from_include_paths(self, watcher_with_patterns: WatcherConfig) -> None:
        """Should generate paths from script include patterns."""
        paths = generate_test_paths(watcher_with_patterns)
        assert any("Liberty University" in p for p in paths)
        assert any("Harvard Business Review" in p for p in paths)

    def test_generates_from_exclude_paths(self, watcher_with_patterns: WatcherConfig) -> None:
        """Should generate paths from script exclude patterns."""
        paths = generate_test_paths(watcher_with_patterns)
        assert any("Archive" in p for p in paths)

    def test_includes_other_database(self, watcher_with_patterns: WatcherConfig) -> None:
        """Should include a path that doesn't match any patterns."""
        paths = generate_test_paths(watcher_with_patterns)
        assert "Other Database/test.pdf" in paths

    def test_paths_are_sorted(self, watcher_with_patterns: WatcherConfig) -> None:
        """Generated paths should be sorted alphabetically."""
        paths = generate_test_paths(watcher_with_patterns)
        assert paths == sorted(paths)
