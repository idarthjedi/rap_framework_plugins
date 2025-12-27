"""Main entry point for RAP Importer."""

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


def acquire_lock() -> bool:
    """Acquire exclusive lock to ensure single instance.

    Returns:
        True if lock acquired, False if another instance is running.
    """
    global _lock_file_handle

    try:
        _lock_file_handle = open(LOCK_FILE, "w")
        fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Write PID for informational purposes
        _lock_file_handle.write(str(os.getpid()))
        _lock_file_handle.flush()
        return True
    except (IOError, OSError):
        # Lock is held by another process
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

    This is the actual watcher loop, called either directly with --foreground
    or spawned as a daemon by the background mode.

    Args:
        config: Application configuration
        watcher_instances: List of watcher/pipeline pairs

    Returns:
        Exit code
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
        """Process existing files and start watchers after menu bar appears."""
        # Process existing files first (for each watcher)
        for instance in watcher_instances:
            existing = scan_existing_files(instance.config.watch)
            if existing:
                logger.info(f"[{instance.name}] Processing {len(existing)} existing files")
                for file_path in existing:
                    instance.pipeline.process_file(file_path)

        # Start all watchers
        for instance in watcher_instances:
            instance.watcher.start()
            logger.info(f"[{instance.name}] Started watching: {instance.config.watch.base_folder}")

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
