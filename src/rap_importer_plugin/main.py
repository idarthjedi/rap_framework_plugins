"""Main entry point for RAP Importer.

Execution Modes
---------------
The application supports three execution modes:

1. BACKGROUND (default): "Trampoline" mode - validates config, checks the lock,
   then spawns itself with --foreground as a detached daemon process. Returns
   immediately to the terminal. Logs go to file, not terminal.

   Usage: uv run rap-importer

2. FOREGROUND: The actual worker mode - runs the menu bar app, file watchers,
   and pipeline processing. Blocks until quit. Used for debugging (see logs in
   real-time) or when managed by launchd/systemd.

   Usage: uv run rap-importer --foreground

3. RUNONCE: Process all existing files in watch folders and exit. Returns
   exit code 0 if all succeeded, 1 if any failed. Useful for CI/scripts.

   Usage: uv run rap-importer --runonce

The key insight: FOREGROUND mode is the actual worker - BACKGROUND mode just
spawns it detached and returns to the terminal.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

# Lock file to ensure single instance
LOCK_FILE = Path.home() / ".rap-importer.lock"
_lock_file_handle = None  # Keep reference to prevent garbage collection

from .cli import ExecutionMode, parse_args
from .config import find_config_file, load_config
from .executor import ScriptExecutor
from .logging_config import get_logger, setup_logging
from .notifications import setup_notifications
from .pipeline import PipelineManager
from .watcher import FileWatcher, scan_existing_files

if TYPE_CHECKING:
    from .config import Config, WatcherConfig

logger = get_logger("main")


@dataclass
class WatcherInstance:
    """Runtime instance of a watcher with its pipeline."""

    name: str
    watcher: FileWatcher
    pipeline: PipelineManager
    config: WatcherConfig


def _is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is running.

    Args:
        pid: Process ID to check

    Returns:
        True if process is running, False otherwise
    """
    try:
        # Signal 0 doesn't send anything, just checks if process exists
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        # Process doesn't exist
        return False
    except PermissionError:
        # Process exists but we don't have permission (still running)
        return True


def _try_acquire_lock() -> bool:
    """Attempt to acquire the lock file.

    Returns:
        True if lock acquired, False if held by another process.
    """
    global _lock_file_handle

    try:
        _lock_file_handle = open(LOCK_FILE, "w")
        fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Write PID for later stale lock detection
        _lock_file_handle.write(str(os.getpid()))
        _lock_file_handle.flush()
        return True
    except (IOError, OSError):
        # Lock is held by another process
        if _lock_file_handle:
            _lock_file_handle.close()
            _lock_file_handle = None
        return False


def acquire_lock() -> bool:
    """Acquire exclusive lock to ensure single instance.

    If the lock file exists but the owning process is no longer running
    (stale lock from crash), the stale lock is removed and a fresh lock
    is acquired.

    Returns:
        True if lock acquired, False if another instance is running.
    """
    # First attempt
    if _try_acquire_lock():
        return True

    # Lock failed - check if it's stale
    try:
        with open(LOCK_FILE, "r") as f:
            pid_str = f.read().strip()
            if pid_str:
                pid = int(pid_str)
                if not _is_process_running(pid):
                    # Stale lock - process is gone, remove and retry
                    LOCK_FILE.unlink(missing_ok=True)
                    return _try_acquire_lock()
    except (IOError, OSError, ValueError):
        # Can't read PID or invalid format - try removing anyway
        try:
            LOCK_FILE.unlink(missing_ok=True)
            return _try_acquire_lock()
        except (IOError, OSError):
            pass

    return False


def release_lock() -> None:
    """Release the lock file."""
    global _lock_file_handle

    if _lock_file_handle:
        try:
            fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_UN)
            _lock_file_handle.close()
        except (IOError, OSError):
            pass
        _lock_file_handle = None


def main() -> int:
    """Main entry point.

    Returns:
        Exit code (0 for success, non-zero for error)
    """
    # Parse command-line arguments
    args = parse_args()

    # Load configuration
    try:
        if args.config_path.exists():
            config_path = args.config_path
        else:
            config_path = find_config_file()
        config = load_config(config_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("Create a config/config.json file or specify path with --config", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # Handle simulation mode (before lock acquisition)
    if args.simulate_paths is not None:
        from .simulate import run_simulation
        return run_simulation(config, args.simulate_paths)

    # BACKGROUND mode: check if already running, then spawn daemon and exit
    if args.mode == ExecutionMode.BACKGROUND:
        # Quick check if another instance is running
        if not acquire_lock():
            print("RAP Importer is already running", file=sys.stderr)
            return 1
        # Release lock so spawned daemon can acquire it
        release_lock()
        return spawn_daemon(args, config_path)

    # Acquire lock for foreground/runonce modes
    if not acquire_lock():
        print("RAP Importer is already running", file=sys.stderr)
        return 1

    # Validate we have enabled watchers
    enabled_watchers = config.enabled_watchers
    if not enabled_watchers:
        print("Error: No enabled watchers in config", file=sys.stderr)
        return 1

    # Setup logging (console output auto-detected based on TTY)
    logger_root = setup_logging(config.logging, args.log_level)
    logger.info("RAP Importer starting")
    logger.info(f"Config loaded from: {config_path}")
    logger.info(f"Enabled watchers: {len(enabled_watchers)}")

    # Setup notifications
    setup_notifications(config.notifications)

    # Get project root for script resolution
    # If config is in a "config" subdirectory, go up one more level
    if config_path.parent.name == "config":
        project_root = config_path.parent.parent
    else:
        project_root = config_path.parent

    # Create shared executor
    executor = ScriptExecutor(project_root)

    # Create watcher instances
    watcher_instances: list[WatcherInstance] = []
    for watcher_config in enabled_watchers:
        pipeline = PipelineManager(
            pipeline_config=watcher_config.pipeline,
            watch_config=watcher_config.watch,
            executor=executor,
            global_exclude_paths=watcher_config.global_exclude_paths,
            log_level=config.logging.level,
        )

        watcher = FileWatcher(watcher_config.watch, pipeline.process_file)

        watcher_instances.append(WatcherInstance(
            name=watcher_config.name,
            watcher=watcher,
            pipeline=pipeline,
            config=watcher_config,
        ))

        logger.info(f"Created watcher: {watcher_config.name} -> {watcher_config.watch.base_folder}")

    # Run in appropriate mode
    if args.mode == ExecutionMode.RUNONCE:
        return run_once(config, watcher_instances)
    else:
        # FOREGROUND mode: run with file watcher and menu bar
        return run_foreground(config, watcher_instances)


def run_once(config: Config, watcher_instances: list[WatcherInstance]) -> int:
    """Process all existing files and exit.

    Args:
        config: Application configuration
        watcher_instances: List of watcher/pipeline pairs

    Returns:
        Exit code
    """
    logger.info(f"Running in run-once mode with {len(watcher_instances)} watchers")

    total_files = 0
    total_success = 0

    for instance in watcher_instances:
        # Scan for existing files
        files = scan_existing_files(instance.config.watch)

        if not files:
            logger.info(f"[{instance.name}] No files to process")
            continue

        logger.info(f"[{instance.name}] Found {len(files)} files to process")

        # Process each file
        success_count = 0
        for file_path in files:
            if instance.pipeline.process_file(file_path):
                success_count += 1

        total_files += len(files)
        total_success += success_count

        failed_count = len(files) - success_count
        logger.info(f"[{instance.name}] Complete: {success_count} succeeded, {failed_count} failed")

    # Report overall results
    total_failed = total_files - total_success
    logger.info(f"Overall: {total_success}/{total_files} files processed successfully")

    return 0 if total_failed == 0 else 1


def spawn_daemon(args, config_path: Path) -> int:
    """Spawn the importer as a background daemon and return immediately.

    This is the "trampoline" - it re-invokes this same module with --foreground
    flag, but detached from the terminal. The spawned process becomes the actual
    worker (run_foreground), while this process exits immediately so the user
    gets their terminal back.

    The spawned daemon:
    - Runs in a new session (start_new_session=True) detached from terminal
    - Has stdout/stderr redirected to /dev/null (logs go to file instead)
    - Acquires its own lock (we released ours before spawning)

    Args:
        args: Parsed CLI arguments
        config_path: Path to config file

    Returns:
        Exit code (0 on successful spawn)
    """
    # Build command to run ourselves with --foreground
    cmd = [
        sys.executable,
        "-m",
        "rap_importer_plugin.main",
        "--foreground",
        "--config",
        str(config_path),
    ]

    # Pass through log level if specified
    if args.log_level:
        cmd.extend(["--log-level", args.log_level])

    # Spawn detached process with no terminal I/O
    # On macOS, we need to keep stdout/stderr open for rumps to work,
    # but redirect them to /dev/null so they don't block on terminal
    with open(os.devnull, "w") as devnull:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=devnull,
            stderr=devnull,
            start_new_session=True,  # Detach from terminal session
        )

    print(f"RAP Importer started in background (log: ~/Library/Logs/rap-importer.log)")
    return 0


def run_foreground(config: Config, watcher_instances: list[WatcherInstance]) -> int:
    """Run continuously with file watchers and menu bar (foreground).

    This is the actual worker - the long-running process that:
    - Shows the menu bar icon (via rumps)
    - Watches folders for new files (via watchdog FSEvents)
    - Processes files through the pipeline
    - Handles graceful shutdown on SIGINT/SIGTERM

    Called either:
    - Directly with --foreground (for debugging, launchd, or systemd)
    - Spawned as a detached daemon by spawn_daemon() in background mode

    When debugging, use: uv run rap-importer --foreground --log-level DEBUG
    to see all logs in real-time in the terminal.

    Args:
        config: Application configuration
        watcher_instances: List of watcher/pipeline pairs

    Returns:
        Exit code (0 on clean shutdown)
    """
    # Import menubar lazily to avoid importing rumps in runonce mode
    # (rumps initializes macOS event loop infrastructure that prevents clean exit)
    from .menubar import run_menubar

    logger.info(f"Running in foreground mode with {len(watcher_instances)} watchers")

    # Handle SIGINT/SIGTERM for graceful shutdown
    shutdown_requested = False

    def handle_signal(signum: int, frame) -> None:
        nonlocal shutdown_requested
        if not shutdown_requested:
            shutdown_requested = True
            logger.info(f"Received signal {signum}, shutting down...")
            for instance in watcher_instances:
                instance.watcher.stop()
            sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Startup callback - called after menu bar is visible
    def on_startup() -> None:
        """Start watchers and process existing files after menu bar appears."""
        import threading

        # Start all watchers first (so new files are caught)
        for instance in watcher_instances:
            instance.watcher.start()
            logger.info(f"[{instance.name}] Started watching: {instance.config.watch.base_folder}")

        # Process existing files in background thread to keep UI responsive
        def process_existing() -> None:
            for instance in watcher_instances:
                existing = scan_existing_files(instance.config.watch)
                if existing:
                    logger.info(f"[{instance.name}] Processing {len(existing)} existing files")
                    for file_path in existing:
                        instance.pipeline.process_file(file_path)

        thread = threading.Thread(target=process_existing, daemon=True)
        thread.start()

    # Quit callback
    def on_quit() -> None:
        logger.info("Shutting down from menu bar")
        for instance in watcher_instances:
            instance.watcher.stop()

    # Run menu bar app (blocks until quit)
    # on_startup is called after menu bar appears to process existing files
    log_path = config.logging.expanded_file
    run_menubar(watcher_instances, log_path, on_quit, on_startup)

    return 0


if __name__ == "__main__":
    sys.exit(main())
