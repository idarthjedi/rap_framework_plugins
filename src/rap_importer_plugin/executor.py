"""Script execution for RAP Importer."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .logging_config import get_logger

if TYPE_CHECKING:
    from .config import ScriptConfig

logger = get_logger("executor")

# Default timeout for script execution (5 minutes)
DEFAULT_TIMEOUT = 300


@dataclass
class ExecutionResult:
    """Result of a script execution."""

    success: bool
    output: str
    error: str | None
    duration_ms: int

    def __str__(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        return f"{status} in {self.duration_ms}ms"


@dataclass
class FileVariables:
    """Variables available for script argument substitution."""

    file_path: str  # Full POSIX path
    relative_path: str  # Path relative to watch folder
    filename: str  # Just the filename
    database: str  # First path component (database name)
    group_path: str  # Path between database and filename

    @classmethod
    def from_file(cls, file_path: Path, base_folder: Path) -> FileVariables:
        """Create variables from a file path.

        Args:
            file_path: Full path to the file
            base_folder: Base watch folder

        Returns:
            FileVariables with all computed values
        """
        # Get relative path
        try:
            relative = file_path.relative_to(base_folder)
        except ValueError:
            # File not in base folder, use full path as relative
            relative = file_path

        parts = relative.parts
        filename = relative.name

        # Parse database and group path
        if len(parts) >= 2:
            database = parts[0]
            # Group path is everything between database and filename
            if len(parts) > 2:
                group_path = "/".join(parts[1:-1])
            else:
                group_path = ""
        else:
            database = ""
            group_path = ""

        return cls(
            file_path=str(file_path),
            relative_path=str(relative),
            filename=filename,
            database=database,
            group_path=group_path,
        )

    def as_dict(self) -> dict[str, str]:
        """Return variables as a dictionary for substitution."""
        return {
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "filename": self.filename,
            "database": self.database,
            "group_path": self.group_path,
        }


class ScriptExecutor:
    """Executes AppleScript or Python scripts."""

    def __init__(self, project_root: Path | None = None) -> None:
        """Initialize the executor.

        Args:
            project_root: Root directory for resolving relative script paths
        """
        self.project_root = project_root or Path.cwd()

    def execute(
        self,
        script: ScriptConfig,
        variables: FileVariables,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> ExecutionResult:
        """Execute a script with variable substitution.

        Args:
            script: Script configuration
            variables: Variables for argument substitution
            timeout: Maximum execution time in seconds

        Returns:
            ExecutionResult with success status and output
        """
        logger.debug(f"Executing script: {script.name} ({script.type})")

        # Resolve script path
        script_path = self._resolve_path(script.path)
        if not script_path.exists():
            return ExecutionResult(
                success=False,
                output="",
                error=f"Script not found: {script_path}",
                duration_ms=0,
            )

        # Substitute variables in args
        substituted_args = self._substitute_args(script.args, variables)

        # Execute based on type
        if script.type == "applescript":
            return self._execute_applescript(script_path, substituted_args, timeout)
        elif script.type == "python":
            return self._execute_python(script_path, substituted_args, timeout)
        else:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Unknown script type: {script.type}",
                duration_ms=0,
            )

    def _resolve_path(self, path: str) -> Path:
        """Resolve a script path relative to project root.

        Args:
            path: Script path (may be relative or absolute)

        Returns:
            Resolved absolute path
        """
        script_path = Path(path)
        if script_path.is_absolute():
            return script_path
        return self.project_root / script_path

    def _substitute_args(
        self,
        args: dict[str, str] | list[str],
        variables: FileVariables,
    ) -> dict[str, str] | list[str]:
        """Substitute variables in script arguments.

        Args:
            args: Original arguments
            variables: Variables for substitution

        Returns:
            Arguments with variables substituted
        """
        var_dict = variables.as_dict()

        if isinstance(args, dict):
            return {
                key: value.format(**var_dict) if isinstance(value, str) else value
                for key, value in args.items()
            }
        else:
            return [
                arg.format(**var_dict) if isinstance(arg, str) else arg
                for arg in args
            ]

    def _execute_applescript(
        self,
        script_path: Path,
        args: dict[str, str] | list[str],
        timeout: int,
    ) -> ExecutionResult:
        """Execute an AppleScript via osascript.

        Args:
            script_path: Path to .scpt or .applescript file
            args: Arguments to pass to the script
            timeout: Maximum execution time

        Returns:
            ExecutionResult
        """
        # Build command
        # For AppleScript, we pass args as positional parameters
        if isinstance(args, dict):
            arg_list = list(args.values())
        else:
            arg_list = args

        cmd = ["osascript", str(script_path)] + arg_list

        logger.trace(f"Running: {' '.join(cmd)}")  # type: ignore[attr-defined]

        return self._run_subprocess(cmd, timeout)

    def _execute_python(
        self,
        script_path: Path,
        args: dict[str, str] | list[str],
        timeout: int,
    ) -> ExecutionResult:
        """Execute a Python script.

        Args:
            script_path: Path to .py file
            args: Arguments to pass to the script
            timeout: Maximum execution time

        Returns:
            ExecutionResult
        """
        # Build command
        if isinstance(args, dict):
            # Convert dict to --key value pairs
            arg_list = []
            for key, value in args.items():
                arg_list.extend([f"--{key}", value])
        else:
            arg_list = args

        cmd = [sys.executable, str(script_path)] + arg_list

        logger.trace(f"Running: {' '.join(cmd)}")  # type: ignore[attr-defined]

        return self._run_subprocess(cmd, timeout)

    def _run_subprocess(self, cmd: list[str], timeout: int) -> ExecutionResult:
        """Run a subprocess and capture results.

        Args:
            cmd: Command to run
            timeout: Maximum execution time

        Returns:
            ExecutionResult
        """
        start_time = time.time()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            if result.returncode == 0:
                logger.trace(f"stdout: {result.stdout.strip()}")  # type: ignore[attr-defined]
                return ExecutionResult(
                    success=True,
                    output=result.stdout.strip(),
                    error=None,
                    duration_ms=duration_ms,
                )
            else:
                error_msg = result.stderr.strip() or f"Exit code: {result.returncode}"
                logger.debug(f"Script failed: {error_msg}")
                return ExecutionResult(
                    success=False,
                    output=result.stdout.strip(),
                    error=error_msg,
                    duration_ms=duration_ms,
                )

        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.warning(f"Script timed out after {timeout}s")
            return ExecutionResult(
                success=False,
                output="",
                error=f"Timeout after {timeout} seconds",
                duration_ms=duration_ms,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Script execution error: {e}")
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                duration_ms=duration_ms,
            )
