"""Main entry point for RAP Importer."""

from __future__ import annotations

import signal
import sys
from pathlib import Path

from .cli import ExecutionMode, parse_args
from .config import find_config_file, load_config
from .executor import ScriptExecutor
from .logging_config import get_logger, setup_logging
from .menubar import run_menubar
from .notifications import setup_notifications
from .pipeline import PipelineManager
from .watcher import FileWatcher, scan_existing_files

logger = get_logger("main")


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
        print("Create a config.json file or specify path with --config", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # Setup logging
    logger_root = setup_logging(config.logging, args.log_level)
    logger.info("RAP Importer starting")
    logger.info(f"Config loaded from: {config_path}")

    # Setup notifications
    setup_notifications(config.notifications)

    # Get project root for script resolution
    project_root = config_path.parent

    # Create components
    executor = ScriptExecutor(project_root)
    pipeline = PipelineManager(
        pipeline_config=config.pipeline,
        watch_config=config.watch,
        executor=executor,
    )

    # Run in appropriate mode
    if args.mode == ExecutionMode.RUNONCE:
        return run_once(config, pipeline)
    else:
        return run_background(config, pipeline)


def run_once(config, pipeline: PipelineManager) -> int:
    """Process all existing files and exit.

    Args:
        config: Application configuration
        pipeline: Pipeline manager

    Returns:
        Exit code
    """
    logger.info("Running in run-once mode")

    # Scan for existing files
    files = scan_existing_files(config.watch)

    if not files:
        logger.info("No files to process")
        return 0

    logger.info(f"Found {len(files)} files to process")

    # Process each file
    success_count = 0
    for file_path in files:
        if pipeline.process_file(file_path):
            success_count += 1

    # Report results
    failed_count = len(files) - success_count
    logger.info(f"Processing complete: {success_count} succeeded, {failed_count} failed")

    return 0 if failed_count == 0 else 1


def run_background(config, pipeline: PipelineManager) -> int:
    """Run continuously with file watcher and menu bar.

    Args:
        config: Application configuration
        pipeline: Pipeline manager

    Returns:
        Exit code
    """
    logger.info("Running in background mode")

    # Create watcher
    watcher = FileWatcher(config.watch, pipeline.process_file)

    # Handle SIGINT/SIGTERM for graceful shutdown
    shutdown_requested = False

    def handle_signal(signum: int, frame) -> None:
        nonlocal shutdown_requested
        if not shutdown_requested:
            shutdown_requested = True
            logger.info(f"Received signal {signum}, shutting down...")
            watcher.stop()
            sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Process existing files first
    existing = scan_existing_files(config.watch)
    if existing:
        logger.info(f"Processing {len(existing)} existing files")
        for file_path in existing:
            pipeline.process_file(file_path)

    # Start watching for new files
    watcher.start()

    # Run menu bar app (blocks until quit)
    def on_quit() -> None:
        logger.info("Shutting down from menu bar")
        watcher.stop()

    log_path = config.logging.expanded_file
    run_menubar(pipeline, log_path, on_quit)

    return 0


if __name__ == "__main__":
    sys.exit(main())
