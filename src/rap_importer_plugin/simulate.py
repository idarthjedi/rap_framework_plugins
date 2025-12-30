"""Path filter simulation for RAP Importer.

This module provides a CLI simulation mode that displays how test file paths
would be processed by the pipeline - showing global exclusions and per-script
include/exclude filtering results.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from .config import Config, ScriptConfig, WatcherConfig


class FilterResult(Enum):
    """Result of path filter evaluation."""

    RUN = "run"  # Script will execute
    EXCLUDED = "excluded"  # Matched an exclude pattern
    SKIPPED = "skipped"  # No include pattern matched (when includes specified)
    GLOBAL_EXCLUDED = "global"  # File globally excluded


@dataclass
class PathEvaluation:
    """Evaluation result for a single path."""

    path: str
    globally_excluded: bool
    script_results: dict[str, FilterResult]


def _pattern_to_example(pattern: str, default_database: str = "SampleDB") -> str:
    """Convert an fnmatch pattern to an example file path.

    Args:
        pattern: fnmatch pattern (e.g., "*/EndNote/*", "Liberty.University/*")
        default_database: Database name to use when pattern starts with */

    Returns:
        Example path that would match the pattern
    """
    path = pattern

    # Handle leading wildcard (replace with default database)
    if path.startswith("*/"):
        path = default_database + "/" + path[2:]

    # Handle trailing wildcard (replace with test.pdf)
    if path.endswith("/*"):
        path = path[:-2] + "/test.pdf"
    elif path.endswith("*"):
        path = path[:-1] + "test.pdf"

    # Handle middle wildcards (replace with sample folder)
    # Match patterns like "Database/*/SubFolder" -> "Database/Sample/SubFolder"
    path = re.sub(r'/\*/', '/Sample/', path)

    # If no file extension, add test.pdf
    if not path.endswith(".pdf"):
        if not path.endswith("/"):
            path += "/"
        path += "test.pdf"

    return path


def _extract_database_from_patterns(watcher: WatcherConfig) -> str | None:
    """Extract a concrete database name from patterns if available.

    Looks for patterns that start with a concrete name (not */) and returns
    the first path component as the database name.

    Args:
        watcher: Watcher configuration

    Returns:
        Database name if found, None otherwise
    """
    # Check script include patterns for concrete database names
    for script in watcher.pipeline.scripts:
        for pattern in script.include_paths:
            if not pattern.startswith("*/"):
                # First component is the database name
                parts = pattern.split("/")
                if parts[0] and "*" not in parts[0]:
                    return parts[0]

    return None


def generate_test_paths(watcher: WatcherConfig) -> list[str]:
    """Generate representative test paths from config patterns.

    Extracts patterns from global_exclude_paths and script include/exclude
    patterns to create example paths that demonstrate the filtering behavior.

    Args:
        watcher: Watcher configuration

    Returns:
        List of example paths sorted alphabetically
    """
    paths: set[str] = set()

    # Try to extract a real database name from patterns
    default_db = _extract_database_from_patterns(watcher) or "SampleDB"

    # From global excludes (show what gets blocked)
    for pattern in watcher.global_exclude_paths:
        paths.add(_pattern_to_example(pattern, default_db))

    # From each script's include/exclude patterns
    for script in watcher.pipeline.scripts:
        for pattern in script.include_paths:
            paths.add(_pattern_to_example(pattern, default_db))
        for pattern in script.exclude_paths:
            paths.add(_pattern_to_example(pattern, default_db))

    # Add standard test case (path that doesn't match any patterns)
    paths.add("Other Database/test.pdf")

    # Add a "typical" path that should run most scripts
    if default_db != "SampleDB":
        paths.add(f"{default_db}/SampleCourse/test.pdf")

    return sorted(paths)


def _check_script_filters(script: ScriptConfig, path: str) -> FilterResult:
    """Check if a script would run for a given path.

    Implements the same logic as PipelineManager._should_run_script().

    Args:
        script: Script configuration
        path: Relative file path

    Returns:
        FilterResult indicating whether script runs or why it's skipped
    """
    # No filters = run on all files
    if not script.include_paths and not script.exclude_paths:
        return FilterResult.RUN

    # Check exclude patterns first (exclude takes precedence)
    for pattern in script.exclude_paths:
        if fnmatch.fnmatch(path, pattern):
            return FilterResult.EXCLUDED

    # If no include patterns, run on all non-excluded files
    if not script.include_paths:
        return FilterResult.RUN

    # Check include patterns - must match at least one
    for pattern in script.include_paths:
        if fnmatch.fnmatch(path, pattern):
            return FilterResult.RUN

    # Didn't match any include pattern
    return FilterResult.SKIPPED


def evaluate_path(path: str, watcher: WatcherConfig) -> PathEvaluation:
    """Evaluate how a path would be processed by the pipeline.

    Args:
        path: Relative file path to evaluate
        watcher: Watcher configuration

    Returns:
        PathEvaluation with global status and per-script results
    """
    # Check global exclude first
    for pattern in watcher.global_exclude_paths:
        if fnmatch.fnmatch(path, pattern):
            return PathEvaluation(
                path=path,
                globally_excluded=True,
                script_results={},
            )

    # Check each enabled script
    script_results: dict[str, FilterResult] = {}
    for script in watcher.pipeline.enabled_scripts:
        script_results[script.name] = _check_script_filters(script, path)

    return PathEvaluation(
        path=path,
        globally_excluded=False,
        script_results=script_results,
    )


def _format_result(result: FilterResult | None, globally_excluded: bool) -> str:
    """Format a filter result for display.

    Args:
        result: Script filter result (or None if globally excluded)
        globally_excluded: Whether file was globally excluded

    Returns:
        Formatted string with color markup
    """
    if globally_excluded:
        return "[dim]-[/dim]"

    if result == FilterResult.RUN:
        return "[green]✓ RUN[/green]"
    elif result == FilterResult.EXCLUDED:
        return "[red]✗ excl[/red]"
    elif result == FilterResult.SKIPPED:
        return "[yellow]✗ skip[/yellow]"
    else:
        return "[dim]?[/dim]"


def run_simulation(config: Config, custom_paths: tuple[str, ...]) -> int:
    """Run the path filter simulation and display results.

    Args:
        config: Application configuration
        custom_paths: Additional paths to test (from CLI arguments)

    Returns:
        Exit code (0 for success)
    """
    console = Console()

    for watcher in config.enabled_watchers:
        # Generate test paths
        paths = generate_test_paths(watcher)

        # Add custom paths
        for custom in custom_paths:
            if custom not in paths:
                paths.append(custom)
        paths = sorted(set(paths))

        # Display watcher info
        global_excludes = ", ".join(watcher.global_exclude_paths) or "(none)"
        console.print(Panel(
            f"[bold]Watcher:[/bold] {watcher.name}\n"
            f"[bold]Global Excludes:[/bold] {global_excludes}",
            expand=False,
        ))

        # Build table
        table = Table(show_header=True, header_style="bold")
        table.add_column("Test Path", style="cyan", no_wrap=False)
        table.add_column("Global", justify="center")

        # Add column for each enabled script (shortened name)
        scripts = list(watcher.pipeline.enabled_scripts)
        for script in scripts:
            # Shorten script names for column headers
            short_name = script.name.split()[0] if " " in script.name else script.name[:12]
            table.add_column(short_name, justify="center")

        # Evaluate each path
        for path in paths:
            evaluation = evaluate_path(path, watcher)

            # Build row
            row = [path]

            # Global status
            if evaluation.globally_excluded:
                row.append("[red]✗ EXCL[/red]")
            else:
                row.append("[green]✓[/green]")

            # Script statuses
            for script in scripts:
                result = evaluation.script_results.get(script.name)
                row.append(_format_result(result, evaluation.globally_excluded))

            table.add_row(*row)

        console.print(table)
        console.print()

        # Legend
        console.print(
            "[dim]Legend: [green]✓ RUN[/green] = script executes, "
            "[red]✗ excl[/red] = excluded by pattern, "
            "[yellow]✗ skip[/yellow] = no include match, "
            "[dim]-[/dim] = globally excluded[/dim]"
        )
        console.print()

    return 0
