"""Pipeline execution management for RAP Importer."""

from __future__ import annotations

import fnmatch
import shutil
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .executor import FileVariables, ScriptExecutor
from .logging_config import get_logger
from .notifications import notify_error, notify_success

if TYPE_CHECKING:
    from .config import PipelineConfig, ScriptConfig, WatchConfig

logger = get_logger("pipeline")


class PipelineManager:
    """Manages execution of script pipeline for each file."""

    def __init__(
        self,
        pipeline_config: PipelineConfig,
        watch_config: WatchConfig,
        executor: ScriptExecutor,
        global_exclude_paths: list[str] | None = None,
        on_success: Callable[[], None] | None = None,
        log_level: str = "INFO",
    ) -> None:
        """Initialize the pipeline manager.

        Args:
            pipeline_config: Pipeline configuration
            watch_config: Watch configuration (for base folder)
            executor: Script executor instance
            global_exclude_paths: Patterns to exclude globally (all scripts and deletion)
            on_success: Optional callback when file is successfully processed
            log_level: Current log level (for variable substitution in scripts)
        """
        self.config = pipeline_config
        self.watch_config = watch_config
        self.executor = executor
        self.on_success = on_success
        self.log_level = log_level

        # Set up global exclude paths, always including _Archived folder
        self.global_exclude_paths = list(global_exclude_paths or [])
        if "_Archived/*" not in self.global_exclude_paths:
            self.global_exclude_paths.append("_Archived/*")

        # Track failed files and their retry counts
        self._failed_files: dict[str, int] = {}
        self._files_processed = 0

        # Track actively processing files (thread-safe)
        self._active_processing = 0
        self._active_lock = threading.Lock()

    @property
    def files_processed(self) -> int:
        """Number of files successfully processed."""
        return self._files_processed

    @property
    def active_processing(self) -> int:
        """Number of files currently being processed (thread-safe)."""
        with self._active_lock:
            return self._active_processing

    def _should_run_script(self, script: ScriptConfig, relative_path: str) -> bool:
        """Check if script should run based on path filters.

        Filtering logic:
        - No filters (both lists empty): script runs on all files
        - Exclude patterns checked first: if any match, script is skipped
        - Include patterns: if specified, at least one must match

        Args:
            script: Script configuration with include/exclude patterns
            relative_path: File path relative to watch folder (e.g., "DB/Group/file.pdf")

        Returns:
            True if script should run for this file
        """
        # No filters = run on all files (backward compatible)
        if not script.include_paths and not script.exclude_paths:
            return True

        # Check exclude patterns first (exclude takes precedence)
        for pattern in script.exclude_paths:
            if fnmatch.fnmatch(relative_path, pattern):
                logger.debug(
                    f"Script '{script.name}' excluded by pattern '{pattern}': {relative_path}"
                )
                return False

        # If no include patterns, run on all non-excluded files
        if not script.include_paths:
            return True

        # Check include patterns - must match at least one
        for pattern in script.include_paths:
            if fnmatch.fnmatch(relative_path, pattern):
                logger.debug(
                    f"Script '{script.name}' included by pattern '{pattern}': {relative_path}"
                )
                return True

        # Didn't match any include pattern
        logger.debug(
            f"Script '{script.name}' skipped (no include pattern matched): {relative_path}"
        )
        return False

    def _is_globally_excluded(self, relative_path: str) -> bool:
        """Check if file matches any global exclude patterns.

        Args:
            relative_path: File path relative to watch folder

        Returns:
            True if file should be excluded globally
        """
        for pattern in self.global_exclude_paths:
            if fnmatch.fnmatch(relative_path, pattern):
                logger.debug(
                    f"File excluded by global pattern '{pattern}': {relative_path}"
                )
                return True
        return False

    def process_file(self, file_path: Path) -> bool:
        """Run all scripts in pipeline for a file.

        Args:
            file_path: Path to the file to process

        Returns:
            True if all scripts succeeded, False otherwise
        """
        file_key = str(file_path)

        # Check if we should skip this file
        if self._should_skip_file(file_key):
            logger.debug(f"Skipping file (max retries exceeded): {file_path}")
            return False

        # Check if file exists
        if not file_path.exists():
            logger.warning(f"File no longer exists: {file_path}")
            return False

        # Compute relative path for filtering checks
        base_folder = self.watch_config.expanded_base_folder
        try:
            relative_path = str(file_path.relative_to(base_folder))
        except ValueError:
            relative_path = file_path.name

        # Check global exclude patterns (before any processing)
        if self._is_globally_excluded(relative_path):
            return False  # Silent skip, already logged at DEBUG

        # Track active processing (for menu bar indicator)
        with self._active_lock:
            self._active_processing += 1

        try:
            return self._do_process_file(file_path, base_folder, relative_path, file_key)
        finally:
            with self._active_lock:
                self._active_processing -= 1

    def _do_process_file(
        self, file_path: Path, base_folder: Path, relative_path: str, file_key: str
    ) -> bool:
        """Internal method that performs the actual file processing.

        Args:
            file_path: Path to the file to process
            base_folder: Watch folder base path
            relative_path: Path relative to watch folder
            file_key: String key for tracking failures

        Returns:
            True if all scripts succeeded, False otherwise
        """
        pipeline_start = time.time()
        logger.info(f"Processing: {file_path.name}")

        # Create variables for substitution
        variables = FileVariables.from_file(file_path, base_folder, self.log_level)

        logger.debug(
            f"Variables: database={variables.database}, "
            f"group_path={variables.group_path}, filename={variables.filename}"
        )

        # Filter scripts by enabled status and path filters
        scripts = [
            s for s in self.config.enabled_scripts
            if self._should_run_script(s, variables.relative_path)
        ]

        if not scripts:
            # No scripts matched - leave file in place for manual review
            logger.info(f"No scripts matched path filters: {variables.relative_path}")
            return False

        for i, script in enumerate(scripts, 1):
            logger.debug(f"Running script {i}/{len(scripts)}: {script.name}")

            result = self.executor.execute(script, variables)

            # Log script execution time
            duration_sec = result.duration_ms / 1000
            logger.info(f"  [{script.name}] completed in {duration_sec:.2f}s")

            # Log any TIMING output from AppleScript (captured in stderr)
            if result.stderr and "TIMING:" in result.stderr:
                for line in result.stderr.splitlines():
                    if line.startswith("TIMING:"):
                        logger.info(f"  [{script.name}] {line}")

            if not result.success:
                logger.error(
                    f"Script '{script.name}' failed: {result.error}"
                )
                # Log stdout if present (may contain useful context)
                if result.output:
                    for line in result.output.splitlines():
                        logger.error(f"  stdout: {line}")

                # Track failure for retry
                self._record_failure(file_key)

                # Notify user
                logger.debug(f"Sending failure notification for: {file_path.name}")
                result_notify = notify_error(
                    "Import Failed",
                    f"{file_path.name}: {result.error or 'Unknown error'}",
                )
                logger.debug(f"Notification result: {result_notify}")

                return False

            # Log script output at INFO level if present
            if result.output:
                for line in result.output.splitlines():
                    logger.info(f"  [{script.name}] {line}")
            else:
                logger.debug(f"Script '{script.name}] result: {result}")

        # All scripts succeeded
        pipeline_elapsed = time.time() - pipeline_start
        logger.info(f"Pipeline complete for: {file_path.name} (total: {pipeline_elapsed:.2f}s)")

        # Archive the original file
        self._archive_file(file_path)

        # Clear any failure tracking
        self._failed_files.pop(file_key, None)

        # Update counter and notify
        self._files_processed += 1

        if self.on_success:
            self.on_success()

        notify_success(
            "Import Complete",
            f"{file_path.name} imported successfully",
        )

        return True

    def _should_skip_file(self, file_key: str) -> bool:
        """Check if file has exceeded retry count.

        Args:
            file_key: File path as string

        Returns:
            True if file should be skipped
        """
        failure_count = self._failed_files.get(file_key, 0)
        return failure_count >= self.config.retry_count

    def _record_failure(self, file_key: str) -> None:
        """Record a failure for a file.

        Args:
            file_key: File path as string
        """
        current = self._failed_files.get(file_key, 0)
        self._failed_files[file_key] = current + 1

        remaining = self.config.retry_count - self._failed_files[file_key]
        if remaining > 0:
            logger.info(f"Will retry ({remaining} attempts remaining)")
        else:
            logger.warning(f"Max retries exceeded, file will be ignored: {file_key}")
            notify_error(
                "Import Failed Permanently",
                f"Max retries exceeded for file. Check logs for details.",
            )

    def _archive_file(self, file_path: Path) -> None:
        """Archive file to _Archived folder after successful processing.

        Args:
            file_path: Path to the file to archive
        """
        base_folder = self.watch_config.expanded_base_folder

        try:
            # Calculate archive path preserving folder structure
            relative = file_path.relative_to(base_folder)
            archive_dir = base_folder / "_Archived" / relative.parent
            archive_dir.mkdir(parents=True, exist_ok=True)

            # Handle name collisions
            dest_path = self._get_unique_archive_path(archive_dir, file_path.name)

            # Move file to archive
            shutil.move(str(file_path), str(dest_path))
            logger.debug(f"Archived: {file_path} -> {dest_path}")

        except ValueError:
            # File not under base_folder - shouldn't happen but handle gracefully
            logger.warning(f"Cannot archive file outside base folder: {file_path}")
        except OSError as e:
            logger.warning(f"Failed to archive file: {e}")

    def _get_unique_archive_path(self, archive_dir: Path, filename: str) -> Path:
        """Get unique path in archive, appending suffix if needed.

        Args:
            archive_dir: Target archive directory
            filename: Original filename

        Returns:
            Unique path for the archived file

        Raises:
            RuntimeError: If all suffixes -000 to -999 are exhausted
        """
        dest = archive_dir / filename
        if not dest.exists():
            return dest

        # Split filename and extension
        stem = dest.stem
        suffix = dest.suffix

        for i in range(1000):
            new_name = f"{stem}-{i:03d}{suffix}"
            dest = archive_dir / new_name
            if not dest.exists():
                return dest

        raise RuntimeError(f"Archive suffix exhausted for {filename} (max -999)")

    def reset_failures(self) -> None:
        """Clear failed files tracking (call on restart)."""
        count = len(self._failed_files)
        self._failed_files.clear()
        if count > 0:
            logger.info(f"Reset failure tracking for {count} files")

    def get_failed_files(self) -> list[str]:
        """Get list of files that have failed.

        Returns:
            List of file paths that have failed
        """
        return list(self._failed_files.keys())

    def retry_failed_files(self) -> int:
        """Attempt to retry all failed files.

        Returns:
            Number of files successfully processed on retry
        """
        failed = list(self._failed_files.keys())
        if not failed:
            return 0

        logger.info(f"Retrying {len(failed)} failed files")
        success_count = 0

        for file_key in failed:
            file_path = Path(file_key)
            if file_path.exists():
                # Reset failure count for this file
                self._failed_files[file_key] = 0

                # Wait before retry
                time.sleep(self.config.retry_delay_seconds)

                if self.process_file(file_path):
                    success_count += 1

        return success_count
