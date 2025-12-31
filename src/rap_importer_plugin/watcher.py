"""File watching logic for RAP Importer."""

from __future__ import annotations

import fnmatch
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .logging_config import get_logger

if TYPE_CHECKING:
    from .config import WatchConfig

logger = get_logger("watcher")


class StabilityCheckHandler(FileSystemEventHandler):
    """Watches for file creation and waits for stability before callback.

    A file is considered "stable" when its size hasn't changed for a
    configured duration. This handles files that are still being written
    (e.g., downloads in progress).
    """

    def __init__(
        self,
        config: WatchConfig,
        on_file_ready: Callable[[Path], None],
    ) -> None:
        """Initialize the handler.

        Args:
            config: Watch configuration
            on_file_ready: Callback when a file is ready for processing
        """
        super().__init__()
        self.config = config
        self.on_file_ready = on_file_ready
        self._pending: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def on_created(self, event: FileCreatedEvent) -> None:
        """Handle file creation events."""
        if event.is_directory:
            return
        self._handle_file(Path(event.src_path))

    def on_modified(self, event: FileModifiedEvent) -> None:
        """Handle file modification events."""
        if event.is_directory:
            return
        # Only process if not already pending
        with self._lock:
            if event.src_path in self._pending:
                return
        self._handle_file(Path(event.src_path))

    def _handle_file(self, file_path: Path) -> None:
        """Start stability check for a file.

        Args:
            file_path: Path to the file
        """
        # Check if file matches patterns
        if not self._matches_patterns(file_path):
            return

        # Skip if already pending
        with self._lock:
            if str(file_path) in self._pending:
                logger.debug(f"File already pending: {file_path}")
                return

            # Start stability check in background thread
            thread = threading.Thread(
                target=self._check_stability,
                args=(file_path,),
                daemon=True,
            )
            self._pending[str(file_path)] = thread
            thread.start()

    def _matches_patterns(self, file_path: Path) -> bool:
        """Check if file matches include patterns and not ignore patterns.

        Pattern matching is case-insensitive (e.g., *.pdf matches .PDF).

        Args:
            file_path: Path to check

        Returns:
            True if file should be processed
        """
        filename = file_path.name
        filename_lower = filename.lower()

        # Check ignore patterns first (case-insensitive)
        for pattern in self.config.ignore_patterns:
            if fnmatch.fnmatch(filename_lower, pattern.lower()):
                logger.debug(f"File ignored by pattern '{pattern}': {filename}")
                return False

        # Check include patterns (case-insensitive)
        for pattern in self.config.file_patterns:
            if fnmatch.fnmatch(filename_lower, pattern.lower()):
                return True

        logger.debug(f"File doesn't match any include pattern: {filename}")
        return False

    def _check_stability(self, file_path: Path) -> None:
        """Wait for file to become stable, then trigger callback.

        Args:
            file_path: Path to the file
        """
        logger.debug(f"Checking stability: {file_path}")

        try:
            start_time = time.time()
            last_size = -1

            while True:
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > self.config.stability_timeout_seconds:
                    logger.warning(
                        f"Stability timeout after {elapsed:.1f}s: {file_path}"
                    )
                    return

                # Check if file still exists
                if not file_path.exists():
                    logger.debug(f"File no longer exists: {file_path}")
                    return

                # Get current size
                try:
                    current_size = file_path.stat().st_size
                except OSError as e:
                    logger.warning(f"Error checking file size: {e}")
                    return

                # Check if stable (same size twice and non-zero)
                if current_size == last_size and current_size > 0:
                    logger.debug(
                        f"File stable after {elapsed:.1f}s (size={current_size}): {file_path}"
                    )
                    break

                last_size = current_size
                time.sleep(self.config.stability_check_seconds)

            # File is stable, trigger callback
            logger.info(f"File ready: {file_path}")
            try:
                self.on_file_ready(file_path)
            except Exception as e:
                logger.error(f"Error in file ready callback: {e}")

        finally:
            # Remove from pending
            with self._lock:
                self._pending.pop(str(file_path), None)


class FileWatcher:
    """Watches a folder for new files matching configured patterns."""

    def __init__(
        self,
        config: WatchConfig,
        on_file_ready: Callable[[Path], None],
    ) -> None:
        """Initialize the watcher.

        Args:
            config: Watch configuration
            on_file_ready: Callback when a file is ready for processing
        """
        self.config = config
        self.on_file_ready = on_file_ready
        self._observer: Observer | None = None
        self._handler = StabilityCheckHandler(config, on_file_ready)

    def start(self) -> None:
        """Start watching for files (non-blocking)."""
        if self._observer is not None:
            logger.warning("Watcher already started")
            return

        base_folder = self.config.expanded_base_folder

        # Ensure watch folder exists
        if not base_folder.exists():
            logger.info(f"Creating watch folder: {base_folder}")
            base_folder.mkdir(parents=True, exist_ok=True)

        self._observer = Observer()
        self._observer.schedule(
            self._handler,
            str(base_folder),
            recursive=True,
        )
        self._observer.start()
        logger.info(f"Started watching: {base_folder}")

    def stop(self) -> None:
        """Stop watching and cleanup."""
        if self._observer is None:
            return

        logger.info("Stopping file watcher")
        self._observer.stop()
        self._observer.join(timeout=5)
        self._observer = None

    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._observer is not None and self._observer.is_alive()


def scan_existing_files(config: WatchConfig) -> list[Path]:
    """Scan for existing files in the watch folder.

    This is used for initial processing when starting in runonce mode
    or for processing existing files before starting continuous watching.

    Args:
        config: Watch configuration

    Returns:
        List of file paths matching the patterns
    """
    base_folder = config.expanded_base_folder

    if not base_folder.exists():
        logger.info(f"Watch folder doesn't exist: {base_folder}")
        return []

    files: list[Path] = []

    for root, _dirs, filenames in os.walk(base_folder):
        for filename in filenames:
            file_path = Path(root) / filename
            filename_lower = filename.lower()

            # Check ignore patterns (case-insensitive)
            should_ignore = False
            for pattern in config.ignore_patterns:
                if fnmatch.fnmatch(filename_lower, pattern.lower()):
                    should_ignore = True
                    break

            if should_ignore:
                continue

            # Check include patterns (case-insensitive)
            for pattern in config.file_patterns:
                if fnmatch.fnmatch(filename_lower, pattern.lower()):
                    files.append(file_path)
                    break

    logger.info(f"Found {len(files)} existing files in {base_folder}")
    return sorted(files, key=lambda p: p.stat().st_mtime)
