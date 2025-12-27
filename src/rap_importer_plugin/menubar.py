"""macOS menu bar application for RAP Importer."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import rumps

from .logging_config import get_logger

if TYPE_CHECKING:
    from .main import WatcherInstance

logger = get_logger("menubar")


class RAPImporterMenuBar(rumps.App):
    """macOS menu bar app for background mode."""

    def __init__(
        self,
        watcher_instances: list[WatcherInstance],
        log_path: Path,
        on_quit: Callable[[], None],
        on_startup: Callable[[], None] | None = None,
    ) -> None:
        """Initialize the menu bar app.

        Args:
            watcher_instances: List of watcher/pipeline pairs
            log_path: Path to log file (for "Open Log" action)
            on_quit: Callback to run when quitting
            on_startup: Callback to run after menu bar appears (for deferred init)
        """
        # Use a simple text icon (📄 could be used but text is more reliable)
        super().__init__("RAP", quit_button=None)

        self.watcher_instances = watcher_instances
        self.log_path = log_path
        self.on_quit = on_quit
        self.on_startup = on_startup
        self._startup_done = False
        self._last_count = 0
        self._last_counts: dict[str, int] = {}

        # Build menu
        self._build_menu()

        logger.debug(f"Menu bar app initialized with {len(watcher_instances)} watchers")

    def _build_menu(self) -> None:
        """Build the menu structure."""
        # Status item (non-clickable)
        watcher_count = len(self.watcher_instances)
        self.status_item = rumps.MenuItem(f"RAP Importer - {watcher_count} watchers")

        # Aggregate counter item (non-clickable)
        self.counter_item = rumps.MenuItem("Files processed: 0")

        # Individual watcher items (non-clickable)
        self.watcher_items: dict[str, rumps.MenuItem] = {}
        for instance in self.watcher_instances:
            item = rumps.MenuItem(f"  {instance.name}: 0")
            self.watcher_items[instance.name] = item

        # Open log file
        self.log_item = rumps.MenuItem("Open Log File", callback=self._open_log)

        # Quit
        self.quit_item = rumps.MenuItem("Quit", callback=self._quit)

        # Build menu list
        menu_items: list[rumps.MenuItem | None] = [
            self.status_item,
            self.counter_item,
        ]

        # Add watcher items
        for item in self.watcher_items.values():
            menu_items.append(item)

        menu_items.extend([
            None,  # Separator
            self.log_item,
            self.quit_item,
        ])

        self.menu = menu_items

    @rumps.timer(0.5)
    def _startup_timer(self, sender: rumps.Timer) -> None:
        """Run startup callback once after menu bar is visible."""
        if self._startup_done:
            sender.stop()
            return

        self._startup_done = True
        sender.stop()

        if self.on_startup:
            logger.debug("Running deferred startup callback")
            self.on_startup()

    @rumps.timer(2)
    def _update_counter(self, _sender: rumps.Timer) -> None:
        """Periodically update the file counters."""
        # Calculate aggregate count
        total = sum(inst.pipeline.files_processed for inst in self.watcher_instances)

        if total != self._last_count:
            self._last_count = total
            self.counter_item.title = f"Files processed: {total}"
            logger.debug(f"Updated total counter: {total}")

        # Update individual watcher counts
        for instance in self.watcher_instances:
            count = instance.pipeline.files_processed
            if self._last_counts.get(instance.name) != count:
                self._last_counts[instance.name] = count
                self.watcher_items[instance.name].title = f"  {instance.name}: {count}"

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
    watcher_instances: list[WatcherInstance],
    log_path: Path,
    on_quit: Callable[[], None],
    on_startup: Callable[[], None] | None = None,
) -> None:
    """Run the menu bar application.

    This function blocks until the user quits the app.

    Args:
        watcher_instances: List of watcher/pipeline pairs
        log_path: Path to log file
        on_quit: Callback to run when quitting
        on_startup: Callback to run after menu bar appears (for deferred init)
    """
    app = RAPImporterMenuBar(watcher_instances, log_path, on_quit, on_startup)
    logger.info(f"Starting menu bar app with {len(watcher_instances)} watchers")
    app.run()
