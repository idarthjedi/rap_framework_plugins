"""macOS menu bar application for RAP Importer."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import rumps

from .logging_config import get_logger

if TYPE_CHECKING:
    from .pipeline import PipelineManager

logger = get_logger("menubar")


class RAPImporterMenuBar(rumps.App):
    """macOS menu bar app for background mode."""

    def __init__(
        self,
        pipeline: PipelineManager,
        log_path: Path,
        on_quit: Callable[[], None],
    ) -> None:
        """Initialize the menu bar app.

        Args:
            pipeline: Pipeline manager for file count
            log_path: Path to log file (for "Open Log" action)
            on_quit: Callback to run when quitting
        """
        # Use a simple text icon (📄 could be used but text is more reliable)
        super().__init__("RAP", quit_button=None)

        self.pipeline = pipeline
        self.log_path = log_path
        self.on_quit = on_quit
        self._last_count = 0

        # Build menu
        self._build_menu()

        logger.debug("Menu bar app initialized")

    def _build_menu(self) -> None:
        """Build the menu structure."""
        # Status item (non-clickable)
        self.status_item = rumps.MenuItem("RAP Importer - Running")

        # Counter item (non-clickable)
        self.counter_item = rumps.MenuItem("Files processed: 0")

        # Separator
        separator = None

        # Open log file
        self.log_item = rumps.MenuItem("Open Log File", callback=self._open_log)

        # Quit
        self.quit_item = rumps.MenuItem("Quit", callback=self._quit)

        # Set up menu
        self.menu = [
            self.status_item,
            self.counter_item,
            separator,
            self.log_item,
            self.quit_item,
        ]

    @rumps.timer(2)
    def _update_counter(self, _sender: rumps.Timer) -> None:
        """Periodically update the file counter."""
        count = self.pipeline.files_processed
        if count != self._last_count:
            self._last_count = count
            self.counter_item.title = f"Files processed: {count}"
            logger.debug(f"Updated counter: {count}")

    def _open_log(self, _sender: rumps.MenuItem) -> None:
        """Open the log file in Console.app."""
        logger.debug(f"Opening log file: {self.log_path}")
        try:
            subprocess.run(
                ["open", "-a", "Console", str(self.log_path)],
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to open log file: {e}")
            # Fall back to opening in default text editor
            try:
                subprocess.run(
                    ["open", str(self.log_path)],
                    capture_output=True,
                    check=True,
                )
            except subprocess.CalledProcessError:
                pass

    def _quit(self, _sender: rumps.MenuItem) -> None:
        """Gracefully quit the application."""
        logger.info("Quit requested from menu bar")

        # Update status to show we're shutting down
        self.status_item.title = "RAP Importer - Stopping..."

        # Call the quit callback
        try:
            self.on_quit()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

        # Quit rumps app
        rumps.quit_application()


def run_menubar(
    pipeline: PipelineManager,
    log_path: Path,
    on_quit: Callable[[], None],
) -> None:
    """Run the menu bar application.

    This function blocks until the user quits the app.

    Args:
        pipeline: Pipeline manager for file count
        log_path: Path to log file
        on_quit: Callback to run when quitting
    """
    app = RAPImporterMenuBar(pipeline, log_path, on_quit)
    logger.info("Starting menu bar app")
    app.run()
