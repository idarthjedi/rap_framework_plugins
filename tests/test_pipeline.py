"""Tests for pipeline path filtering and archiving."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rap_importer_plugin.config import (
    PipelineConfig,
    ScriptConfig,
    WatchConfig,
)
from rap_importer_plugin.pipeline import PipelineManager


@pytest.fixture
def mock_executor() -> MagicMock:
    """Create a mock script executor."""
    return MagicMock()


@pytest.fixture
def watch_config() -> WatchConfig:
    """Create a minimal watch config."""
    return WatchConfig(base_folder="~/Downloads/RAP")


def create_pipeline_manager(
    scripts: list[ScriptConfig],
    watch_config: WatchConfig,
    executor: MagicMock,
) -> PipelineManager:
    """Helper to create a PipelineManager with given scripts."""
    pipeline_config = PipelineConfig(scripts=scripts)
    return PipelineManager(
        pipeline_config=pipeline_config,
        watch_config=watch_config,
        executor=executor,
    )


class TestShouldRunScript:
    """Tests for _should_run_script path filtering logic."""

    def test_no_filters_runs_all(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Script with no filters should run for all files."""
        script = ScriptConfig(
            name="No Filters",
            type="python",
            path="test.py",
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        # Should run for any path
        assert pm._should_run_script(script, "Database/Group/file.pdf") is True
        assert pm._should_run_script(script, "Any/Path/Here/file.pdf") is True
        assert pm._should_run_script(script, "file.pdf") is True

    def test_include_pattern_matches(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Script with include pattern should run when pattern matches."""
        script = ScriptConfig(
            name="BUSI Only",
            type="python",
            path="test.py",
            include_paths=["*/BUSI*/*"],
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        # Should match BUSI courses
        assert pm._should_run_script(script, "Liberty/BUSI770/Week01/file.pdf") is True
        assert pm._should_run_script(script, "Liberty/BUSI600/file.pdf") is True

    def test_include_pattern_no_match(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Script with include pattern should skip when pattern doesn't match."""
        script = ScriptConfig(
            name="BUSI Only",
            type="python",
            path="test.py",
            include_paths=["*/BUSI*/*"],
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        # Should not match non-BUSI courses
        assert pm._should_run_script(script, "Liberty/DISS900/Week01/file.pdf") is False
        assert pm._should_run_script(script, "Liberty/CHEM101/file.pdf") is False

    def test_multiple_include_patterns(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Script should run if any include pattern matches."""
        script = ScriptConfig(
            name="BUSI or DISS",
            type="python",
            path="test.py",
            include_paths=["*/BUSI*/*", "*/DISS*/*"],
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        # Should match both BUSI and DISS
        assert pm._should_run_script(script, "Liberty/BUSI770/file.pdf") is True
        assert pm._should_run_script(script, "Liberty/DISS900/file.pdf") is True
        # Should not match others
        assert pm._should_run_script(script, "Liberty/CHEM101/file.pdf") is False

    def test_exclude_pattern_blocks(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Script with exclude pattern should skip when pattern matches."""
        script = ScriptConfig(
            name="No Archive",
            type="python",
            path="test.py",
            exclude_paths=["*/Archive/*"],
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        # Should block Archive folders
        assert pm._should_run_script(script, "Liberty/BUSI770/Archive/file.pdf") is False
        assert pm._should_run_script(script, "Database/Archive/old.pdf") is False

        # Should allow non-Archive folders
        assert pm._should_run_script(script, "Liberty/BUSI770/Week01/file.pdf") is True

    def test_multiple_exclude_patterns(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Script should skip if any exclude pattern matches."""
        script = ScriptConfig(
            name="No Archive or Drafts",
            type="python",
            path="test.py",
            exclude_paths=["*/Archive/*", "*/Drafts/*"],
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        # Should block both Archive and Drafts
        assert pm._should_run_script(script, "Liberty/Archive/file.pdf") is False
        assert pm._should_run_script(script, "Liberty/Drafts/file.pdf") is False
        # Should allow others
        assert pm._should_run_script(script, "Liberty/Active/file.pdf") is True

    def test_exclude_takes_precedence(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Exclude patterns should take precedence over include patterns."""
        script = ScriptConfig(
            name="BUSI except Drafts",
            type="python",
            path="test.py",
            include_paths=["*/BUSI*/*"],
            exclude_paths=["*/Drafts/*"],
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        # Should run for BUSI courses
        assert pm._should_run_script(script, "Liberty/BUSI770/Week01/file.pdf") is True

        # Should block Drafts even within BUSI (exclude wins)
        assert pm._should_run_script(script, "Liberty/BUSI770/Drafts/file.pdf") is False

        # Should not run for non-BUSI (include required)
        assert pm._should_run_script(script, "Liberty/CHEM101/Week01/file.pdf") is False

    def test_only_exclude_no_include(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Script with only exclude patterns should run on all non-excluded files."""
        script = ScriptConfig(
            name="All except Archive",
            type="python",
            path="test.py",
            exclude_paths=["*/Archive/*"],
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        # Should run for any non-Archive path
        assert pm._should_run_script(script, "Liberty/BUSI770/file.pdf") is True
        assert pm._should_run_script(script, "Other/Random/file.pdf") is True

        # Should block Archive
        assert pm._should_run_script(script, "Liberty/Archive/file.pdf") is False

    def test_database_level_pattern(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Pattern matching at database level should work."""
        script = ScriptConfig(
            name="Liberty Only",
            type="python",
            path="test.py",
            include_paths=["Liberty*/*"],
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        # Should match Liberty database
        assert pm._should_run_script(script, "Liberty.University/BUSI770/file.pdf") is True
        assert pm._should_run_script(script, "Liberty/Course/file.pdf") is True

        # Should not match other databases
        assert pm._should_run_script(script, "OtherSchool/Course/file.pdf") is False

    def test_wildcard_patterns(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Various wildcard patterns should work correctly."""
        # Test ? wildcard (single character)
        script = ScriptConfig(
            name="Week Pattern",
            type="python",
            path="test.py",
            include_paths=["*/Week0?/*"],
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        assert pm._should_run_script(script, "DB/Week01/file.pdf") is True
        assert pm._should_run_script(script, "DB/Week09/file.pdf") is True
        assert pm._should_run_script(script, "DB/Week10/file.pdf") is False

    def test_deep_nested_path(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Patterns should work with deeply nested paths."""
        script = ScriptConfig(
            name="Deep Nested",
            type="python",
            path="test.py",
            include_paths=["*/Course/*/Assignments/*"],
        )
        pm = create_pipeline_manager([script], watch_config, mock_executor)

        assert pm._should_run_script(
            script, "DB/Course/Unit1/Assignments/file.pdf"
        ) is True
        assert pm._should_run_script(script, "DB/Course/Unit1/Reading/file.pdf") is False


class TestGlobalExcludePaths:
    """Tests for _is_globally_excluded path filtering."""

    def test_no_global_excludes_allows_all(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """No global excludes should allow all files."""
        pm = create_pipeline_manager([], watch_config, mock_executor)
        # Explicitly set empty global excludes
        pm.global_exclude_paths = []

        assert pm._is_globally_excluded("any/path/file.pdf") is False
        assert pm._is_globally_excluded("Liberty/EndNote/file.pdf") is False

    def test_global_exclude_pattern_matches(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Global exclude pattern should match and exclude files."""
        pm = create_pipeline_manager([], watch_config, mock_executor)
        pm.global_exclude_paths = ["*/EndNote/*"]

        assert pm._is_globally_excluded("Liberty/EndNote/file.pdf") is True
        assert pm._is_globally_excluded("Liberty/EndNote/Sub/file.pdf") is True

    def test_global_exclude_pattern_no_match(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Non-matching paths should not be excluded."""
        pm = create_pipeline_manager([], watch_config, mock_executor)
        pm.global_exclude_paths = ["*/EndNote/*"]

        assert pm._is_globally_excluded("Liberty/BUSI770/file.pdf") is False
        assert pm._is_globally_excluded("Liberty/Course/Week01/file.pdf") is False

    def test_global_exclude_multiple_patterns(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Any matching global exclude pattern should exclude."""
        pm = create_pipeline_manager([], watch_config, mock_executor)
        pm.global_exclude_paths = ["*/EndNote/*", "*/Staging/*"]

        assert pm._is_globally_excluded("Liberty/EndNote/file.pdf") is True
        assert pm._is_globally_excluded("Liberty/Staging/file.pdf") is True
        assert pm._is_globally_excluded("Liberty/BUSI770/file.pdf") is False

    def test_global_exclude_with_database_pattern(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Database-level patterns should work."""
        pm = create_pipeline_manager([], watch_config, mock_executor)
        pm.global_exclude_paths = ["Temp*/*"]

        assert pm._is_globally_excluded("TempFiles/import/file.pdf") is True
        assert pm._is_globally_excluded("Liberty/Course/file.pdf") is False


class TestArchivedFolderAutoExclude:
    """Tests for automatic _Archived folder exclusion."""

    def test_archived_folder_auto_excluded(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """_Archived folder should be automatically added to global excludes."""
        pm = create_pipeline_manager([], watch_config, mock_executor)

        assert "_Archived/*" in pm.global_exclude_paths

    def test_archived_folder_excluded_with_other_patterns(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """_Archived should be added alongside user-defined patterns."""
        pipeline_config = PipelineConfig(scripts=[])
        pm = PipelineManager(
            pipeline_config=pipeline_config,
            watch_config=watch_config,
            executor=mock_executor,
            global_exclude_paths=["*/EndNote/*"],
        )

        assert "_Archived/*" in pm.global_exclude_paths
        assert "*/EndNote/*" in pm.global_exclude_paths

    def test_archived_folder_not_duplicated(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """_Archived should not be added if already present."""
        pipeline_config = PipelineConfig(scripts=[])
        pm = PipelineManager(
            pipeline_config=pipeline_config,
            watch_config=watch_config,
            executor=mock_executor,
            global_exclude_paths=["_Archived/*", "*/Other/*"],
        )

        # Count occurrences
        count = pm.global_exclude_paths.count("_Archived/*")
        assert count == 1


class TestArchiveFile:
    """Tests for file archiving functionality."""

    def test_get_unique_archive_path_no_collision(
        self, tmp_path: Path, mock_executor: MagicMock
    ) -> None:
        """Should return original path when no collision exists."""
        watch_config = WatchConfig(base_folder=str(tmp_path))
        pm = create_pipeline_manager([], watch_config, mock_executor)

        archive_dir = tmp_path / "_Archived" / "Database"
        archive_dir.mkdir(parents=True)

        result = pm._get_unique_archive_path(archive_dir, "document.pdf")

        assert result == archive_dir / "document.pdf"

    def test_get_unique_archive_path_with_collision(
        self, tmp_path: Path, mock_executor: MagicMock
    ) -> None:
        """Should append -000 suffix when file exists."""
        watch_config = WatchConfig(base_folder=str(tmp_path))
        pm = create_pipeline_manager([], watch_config, mock_executor)

        archive_dir = tmp_path / "_Archived" / "Database"
        archive_dir.mkdir(parents=True)

        # Create existing file
        existing = archive_dir / "document.pdf"
        existing.write_text("existing")

        result = pm._get_unique_archive_path(archive_dir, "document.pdf")

        assert result == archive_dir / "document-000.pdf"

    def test_get_unique_archive_path_increments_suffix(
        self, tmp_path: Path, mock_executor: MagicMock
    ) -> None:
        """Should increment suffix when multiple collisions exist."""
        watch_config = WatchConfig(base_folder=str(tmp_path))
        pm = create_pipeline_manager([], watch_config, mock_executor)

        archive_dir = tmp_path / "_Archived" / "Database"
        archive_dir.mkdir(parents=True)

        # Create existing files (suffix starts at -000)
        (archive_dir / "document.pdf").write_text("original")
        (archive_dir / "document-000.pdf").write_text("first copy")
        (archive_dir / "document-001.pdf").write_text("second copy")

        result = pm._get_unique_archive_path(archive_dir, "document.pdf")

        assert result == archive_dir / "document-002.pdf"

    def test_get_unique_archive_path_preserves_extension(
        self, tmp_path: Path, mock_executor: MagicMock
    ) -> None:
        """Suffix should go before extension."""
        watch_config = WatchConfig(base_folder=str(tmp_path))
        pm = create_pipeline_manager([], watch_config, mock_executor)

        archive_dir = tmp_path / "_Archived" / "Database"
        archive_dir.mkdir(parents=True)

        # Create existing file
        (archive_dir / "report.docx").write_text("existing")

        result = pm._get_unique_archive_path(archive_dir, "report.docx")

        assert result == archive_dir / "report-000.docx"
        assert result.suffix == ".docx"

    def test_archive_file_creates_directory(
        self, tmp_path: Path, mock_executor: MagicMock
    ) -> None:
        """Archive should create necessary directories."""
        watch_config = WatchConfig(base_folder=str(tmp_path))
        pm = create_pipeline_manager([], watch_config, mock_executor)

        # Create source file
        source_dir = tmp_path / "Database" / "Course"
        source_dir.mkdir(parents=True)
        source_file = source_dir / "document.pdf"
        source_file.write_text("content")

        pm._archive_file(source_file)

        # Check archive directory was created
        archive_dir = tmp_path / "_Archived" / "Database" / "Course"
        assert archive_dir.exists()
        assert (archive_dir / "document.pdf").exists()

    def test_archive_file_preserves_relative_path(
        self, tmp_path: Path, mock_executor: MagicMock
    ) -> None:
        """Archived files should preserve folder structure."""
        watch_config = WatchConfig(base_folder=str(tmp_path))
        pm = create_pipeline_manager([], watch_config, mock_executor)

        # Create deeply nested source
        source_dir = tmp_path / "Liberty" / "BUSI770" / "Week01"
        source_dir.mkdir(parents=True)
        source_file = source_dir / "assignment.pdf"
        source_file.write_text("content")

        pm._archive_file(source_file)

        # Check file is in correct location
        archived = tmp_path / "_Archived" / "Liberty" / "BUSI770" / "Week01" / "assignment.pdf"
        assert archived.exists()
        assert not source_file.exists()  # Original should be moved

    def test_archive_file_handles_collision(
        self, tmp_path: Path, mock_executor: MagicMock
    ) -> None:
        """Should handle collision when archiving same filename twice."""
        watch_config = WatchConfig(base_folder=str(tmp_path))
        pm = create_pipeline_manager([], watch_config, mock_executor)

        source_dir = tmp_path / "Database"
        source_dir.mkdir(parents=True)

        # First file
        file1 = source_dir / "document.pdf"
        file1.write_text("first content")
        pm._archive_file(file1)

        # Second file with same name
        file2 = source_dir / "document.pdf"
        file2.write_text("second content")
        pm._archive_file(file2)

        archive_dir = tmp_path / "_Archived" / "Database"
        assert (archive_dir / "document.pdf").exists()
        assert (archive_dir / "document-000.pdf").exists()  # Suffix starts at -000
        assert (archive_dir / "document.pdf").read_text() == "first content"
        assert (archive_dir / "document-000.pdf").read_text() == "second content"


class TestActiveProcessing:
    """Tests for active processing counter."""

    def test_active_processing_starts_at_zero(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Active processing count should start at zero."""
        pm = create_pipeline_manager([], watch_config, mock_executor)
        assert pm.active_processing == 0

    def test_active_processing_is_thread_safe(
        self, mock_executor: MagicMock, watch_config: WatchConfig
    ) -> None:
        """Active processing property should use lock for thread safety."""
        pm = create_pipeline_manager([], watch_config, mock_executor)

        # Verify lock exists
        assert hasattr(pm, "_active_lock")
        assert pm._active_processing == 0

        # Manually increment (simulating what process_file does)
        with pm._active_lock:
            pm._active_processing += 1
        assert pm.active_processing == 1

        # Decrement
        with pm._active_lock:
            pm._active_processing -= 1
        assert pm.active_processing == 0
