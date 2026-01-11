"""macOS menu bar application for RAP Importer."""

from __future__ import annotations

import subprocess
import threading
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
        self._last_failed_count = 0
        self._last_active_count = 0
        self._retry_pending = 0  # Track files pending in retry queue
        self._retry_lock = threading.Lock()
        self._startup_pending = 0  # Track files pending at startup
        self._startup_lock = threading.Lock()
        self._manual_pending = 0  # Track manual pipeline runs in progress
        self._manual_lock = threading.Lock()

        # Separate auto and manual watchers
        self._auto_watchers = [w for w in watcher_instances if not w.is_manual]
        self._manual_watchers = [w for w in watcher_instances if w.is_manual]

        # Build menu
        self._build_menu()

        logger.debug(f"Menu bar app initialized with {len(watcher_instances)} watchers")
        logger.debug(f"Auto watchers: {len(self._auto_watchers)}, Manual watchers: {len(self._manual_watchers)}")
        if self._manual_watchers:
            logger.debug(f"Manual watcher names: {[w.name for w in self._manual_watchers]}")

    def set_startup_pending(self, count: int) -> None:
        """Set the number of files pending at startup."""
        with self._startup_lock:
            self._startup_pending = count
        logger.info(f"Set _startup_pending = {count}")

    def decrement_startup_pending(self) -> None:
        """Decrement the startup pending counter."""
        with self._startup_lock:
            self._startup_pending = max(0, self._startup_pending - 1)
            remaining = self._startup_pending
        logger.debug(f"Decremented _startup_pending to {remaining}")

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

        # Run Manual submenu (only if there are manual watchers)
        self.manual_submenu: rumps.MenuItem | None = None
        if self._manual_watchers:
            self.manual_submenu = rumps.MenuItem("Run Manual")
            for instance in self._manual_watchers:
                callback = self._create_manual_callback(instance)
                item = rumps.MenuItem(instance.name, callback=callback)
                self.manual_submenu.add(item)

        # Open Directory submenu (for all watchers)
        self.open_dir_submenu = rumps.MenuItem("Open Directory")
        for instance in self.watcher_instances:
            callback = self._create_open_directory_callback(instance)
            item = rumps.MenuItem(instance.name, callback=callback)
            self.open_dir_submenu.add(item)

        # Retry failed files
        self.retry_item = rumps.MenuItem("Retry - 0", callback=self._retry)

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

        menu_items.append(None)  # Separator

        # Add Run Manual submenu if there are manual watchers
        if self.manual_submenu:
            menu_items.append(self.manual_submenu)

        # Add Open Directory submenu
        menu_items.append(self.open_dir_submenu)

        menu_items.extend([
            self.retry_item,
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

    @rumps.timer(1)
    def _update_counter(self, _sender: rumps.Timer) -> None:
        """Periodically update the file counters and menu bar title."""
        # Update menu bar title based on active processing + retry/startup/manual pending
        active = sum(inst.pipeline.active_processing for inst in self.watcher_instances)
        with self._retry_lock:
            retry_pending = self._retry_pending
        with self._startup_lock:
            startup_pending = self._startup_pending
        with self._manual_lock:
            manual_pending = self._manual_pending
        # Show the highest of active, retry_pending, startup_pending, or manual_pending
        display_count = max(active, retry_pending, startup_pending, manual_pending)
        if display_count != self._last_active_count:
            self._last_active_count = display_count
            if display_count > 0:
                self.title = f"RAP ({display_count})"
                logger.debug(f"Processing {display_count} file(s) (active={active}, retry={retry_pending}, startup={startup_pending}, manual={manual_pending})")
            else:
                self.title = "RAP"
                logger.debug("Processing complete, title reset")

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

        # Update failed files count
        failed_count = sum(
            len(inst.pipeline.get_failed_files()) for inst in self.watcher_instances
        )
        if failed_count != self._last_failed_count:
            self._last_failed_count = failed_count
            self.retry_item.title = f"Retry - {failed_count}"
            logger.debug(f"Updated failed counter: {failed_count}")

    def _create_manual_callback(self, instance: WatcherInstance) -> Callable:
        """Create a callback function for a manual watcher menu item.

        Args:
            instance: The watcher instance to run

        Returns:
            Callback function for rumps MenuItem
        """
        def callback(_sender: rumps.MenuItem) -> None:
            self._run_manual(instance)
        return callback

    def _run_manual(self, instance: WatcherInstance) -> None:
        """Run a manual pipeline in a background thread.

        Args:
            instance: The watcher instance to run
        """
        logger.info(f"Running manual pipeline: {instance.name}")

        # Set manual pending count for menu bar display
        with self._manual_lock:
            self._manual_pending += 1
        logger.debug(f"Set _manual_pending = {self._manual_pending}")

        # Capture self reference for closure
        menu_bar = self

        def do_manual() -> None:
            try:
                instance.pipeline.run_manual()
            finally:
                # Always decrement pending count, even on error
                with menu_bar._manual_lock:
                    menu_bar._manual_pending = max(0, menu_bar._manual_pending - 1)
                    logger.debug(f"Decremented _manual_pending to {menu_bar._manual_pending}")

        thread = threading.Thread(target=do_manual, daemon=True)
        thread.start()

    def _create_open_directory_callback(self, instance: WatcherInstance) -> Callable:
        """Create a callback function to open a watcher's base folder in Finder.

        Args:
            instance: The watcher instance whose folder to open

        Returns:
            Callback function for rumps MenuItem
        """
        def callback(_sender: rumps.MenuItem) -> None:
            self._open_directory(instance)
        return callback

    def _open_directory(self, instance: WatcherInstance) -> None:
        """Open a watcher's base folder in Finder.

        Args:
            instance: The watcher instance whose folder to open
        """
        folder_path = instance.config.watch.expanded_base_folder
        logger.debug(f"Opening directory: {folder_path}")
        try:
            subprocess.run(["open", str(folder_path)], check=True)
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to open directory {folder_path}: {e}")

    def _retry(self, _sender: rumps.MenuItem) -> None:
        """Retry all failed files by re-dispatching to normal processing flow."""
        # Collect failed files and their watchers before clearing
        files_to_retry: list[tuple[WatcherInstance, Path]] = []
        for instance in self.watcher_instances:
            for file_key in instance.pipeline.get_failed_files():
                file_path = Path(file_key)
                if file_path.exists():
                    files_to_retry.append((instance, file_path))

        if not files_to_retry:
            logger.info("No failed files to retry")
            return

        logger.info(f"Retrying {len(files_to_retry)} failed files")

        # Clear failure tracking so files aren't skipped
        for instance in self.watcher_instances:
            instance.pipeline.reset_failures()

        # Set retry pending count for menu bar display
        with self._retry_lock:
            self._retry_pending = len(files_to_retry)
        logger.info(f"Set _retry_pending = {len(files_to_retry)}")

        # Re-dispatch each file through the normal watcher callback (same as watchdog)
        # Run in background thread to keep UI responsive
        # Capture self reference for closure
        menu_bar = self

        def do_retry() -> None:
            for i, (instance, file_path) in enumerate(files_to_retry, 1):
                logger.debug(f"Retry processing file {i}/{len(files_to_retry)}: {file_path.name}")
                try:
                    instance.pipeline.process_file(file_path)
                finally:
                    # Always decrement pending count, even on error
                    with menu_bar._retry_lock:
                        menu_bar._retry_pending = max(0, menu_bar._retry_pending - 1)
                        logger.debug(f"Decremented _retry_pending to {menu_bar._retry_pending}")

        thread = threading.Thread(target=do_retry, daemon=True)
        thread.start()

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
    on_startup: Callable[[RAPImporterMenuBar], None] | None = None,
) -> None:
    """Run the menu bar application.

    This function blocks until the user quits the app.

    Args:
        watcher_instances: List of watcher/pipeline pairs
        log_path: Path to log file
        on_quit: Callback to run when quitting
        on_startup: Callback to run after menu bar appears, receives app instance
    """
    # Create wrapper that passes app instance to on_startup
    def startup_wrapper() -> None:
        if on_startup:
            on_startup(app)

    app = RAPImporterMenuBar(watcher_instances, log_path, on_quit, startup_wrapper)
    logger.info(f"Starting menu bar app with {len(watcher_instances)} watchers")
    app.run()
