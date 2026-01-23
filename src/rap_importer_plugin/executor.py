"""Script execution for RAP Importer."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .logging_config import get_logger
from .paths import expand_path

if TYPE_CHECKING:
    from .config import ScriptConfig, WatchConfig

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
    stderr: str = ""  # Captured stderr (for timing info, etc.)

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
    base_folder: str = ""  # Base watch folder path
    log_level: str = "INFO"  # Current log level from config

    @classmethod
    def from_file(
        cls,
        file_path: Path,
        base_folder: Path,
        log_level: str = "INFO",
    ) -> FileVariables:
        """Create variables from a file path.

        Args:
            file_path: Full path to the file
            base_folder: Base watch folder
            log_level: Current log level from config

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
            base_folder=str(base_folder),
            log_level=log_level,
        )

    def as_dict(self) -> dict[str, str]:
        """Return variables as a dictionary for substitution."""
        return {
            "file_path": self.file_path,
            "relative_path": self.relative_path,
            "filename": self.filename,
            "database": self.database,
            "group_path": self.group_path,
            "base_folder": self.base_folder,
            "log_level": self.log_level,
        }


@dataclass
class ManualVariables:
    """Variables available for manual trigger script argument substitution.

    Unlike FileVariables, these don't require a specific file - just the base folder.
    Used for manual trigger watchers that run commands on the folder as a whole.
    """

    base_folder: str  # Base watch folder path
    log_level: str = "INFO"  # Current log level from config

    @classmethod
    def from_watch_config(
        cls,
        watch_config: "WatchConfig",
        log_level: str = "INFO",
    ) -> "ManualVariables":
        """Create variables from a watch configuration.

        Args:
            watch_config: Watch configuration with base_folder
            log_level: Current log level from config

        Returns:
            ManualVariables with computed values
        """
        return cls(
            base_folder=str(watch_config.expanded_base_folder),
            log_level=log_level,
        )

    def as_dict(self) -> dict[str, str]:
        """Return variables as a dictionary for substitution."""
        return {
            "base_folder": self.base_folder,
            "log_level": self.log_level,
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

        # Substitute variables in args, path, and cwd
        try:
            substituted_args = self._substitute_args(script.args, variables)
            var_dict = variables.as_dict()

            # Handle command type separately (path is a command string, not a file)
            if script.type == "command":
                substituted_command = self._substitute_string(script.path, var_dict)
                substituted_cwd = self._substitute_string(script.cwd, var_dict) if script.cwd else None
                return self._execute_command(
                    substituted_command, substituted_args, substituted_cwd, timeout
                )
        except ValueError as e:
            # Unknown variable in substitution
            logger.error(f"Variable substitution error in script '{script.name}': {e}")
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                duration_ms=0,
            )

        # For applescript and python, resolve and validate script path
        script_path = self._resolve_path(script.path)
        if not script_path.exists():
            return ExecutionResult(
                success=False,
                output="",
                error=f"Script not found: {script_path}",
                duration_ms=0,
            )

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

    def _substitute_string(
        self,
        value: str,
        var_dict: dict[str, str],
    ) -> str:
        """Substitute variables in a single string.

        Args:
            value: String with {variable} placeholders
            var_dict: Variables dictionary

        Returns:
            String with variables substituted

        Raises:
            ValueError: If an unknown variable is referenced
        """
        available_vars = ", ".join(f"{{{k}}}" for k in sorted(var_dict.keys()))
        try:
            return value.format(**var_dict)
        except KeyError as e:
            unknown_var = str(e).strip("'")
            raise ValueError(
                f"Unknown variable '{{{unknown_var}}}' in: {value}\n"
                f"Available variables: {available_vars}"
            ) from None

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

        Raises:
            ValueError: If an unknown variable is referenced
        """
        var_dict = variables.as_dict()
        available_vars = ", ".join(f"{{{k}}}" for k in sorted(var_dict.keys()))

        def substitute(value: str) -> str:
            try:
                return value.format(**var_dict)
            except KeyError as e:
                unknown_var = str(e).strip("'")
                raise ValueError(
                    f"Unknown variable '{{{unknown_var}}}' in argument: {value}\n"
                    f"Available variables: {available_vars}"
                ) from None

        if isinstance(args, dict):
            return {
                key: substitute(value) if isinstance(value, str) else value
                for key, value in args.items()
            }
        else:
            return [
                substitute(arg) if isinstance(arg, str) else arg
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

        logger.debug(f"Executing: {' '.join(cmd)}")

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

        logger.debug(f"Executing: {' '.join(cmd)}")

        return self._run_subprocess(cmd, timeout)

    def _execute_command(
        self,
        command_str: str,
        args: dict[str, str] | list[str],
        cwd: str | None,
        timeout: int,
    ) -> ExecutionResult:
        """Execute a shell command.

        Args:
            command_str: The command string to execute (already variable-substituted)
            args: Additional arguments to append
            cwd: Working directory (optional, supports ~ expansion)
            timeout: Maximum execution time

        Returns:
            ExecutionResult
        """
        # Parse command string into list (handles quotes, spaces correctly)
        try:
            cmd = shlex.split(command_str)
        except ValueError as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Failed to parse command: {e}",
                duration_ms=0,
            )

        # Append additional args
        if isinstance(args, dict):
            for key, value in args.items():
                cmd.extend([f"--{key}", value])
        else:
            cmd.extend(args)

        # Expand ~ and ${VAR} in all command parts that look like paths
        cmd = [expand_path(part) if part.startswith("~") or "${" in part else part for part in cmd]

        # Resolve working directory
        resolved_cwd: str | None = None
        if cwd:
            resolved_cwd = expand_path(cwd)
            if not os.path.isdir(resolved_cwd):
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Working directory does not exist: {resolved_cwd}",
                    duration_ms=0,
                )

        # Create clean environment without Python/virtualenv variables
        # This allows tools like 'uv' to use their own project's environment
        clean_env = {
            k: v for k, v in os.environ.items()
            if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "CONDA_PREFIX")
        }

        if resolved_cwd:
            logger.debug(f"Executing (cwd={resolved_cwd}): {' '.join(cmd)}")
        else:
            logger.debug(f"Executing: {' '.join(cmd)}")

        return self._run_subprocess(cmd, timeout, cwd=resolved_cwd, env=clean_env)

    def _run_subprocess(
        self,
        cmd: list[str],
        timeout: int,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Run a subprocess and capture results.

        Args:
            cmd: Command to run
            timeout: Maximum execution time
            cwd: Working directory (optional)
            env: Environment variables (optional, defaults to current environment)

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
                cwd=cwd,
                env=env,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            stderr_content = result.stderr.strip() if result.stderr else ""

            if result.returncode == 0:
                logger.trace(f"stdout: {result.stdout.strip()}")  # type: ignore[attr-defined]
                return ExecutionResult(
                    success=True,
                    output=result.stdout.strip(),
                    error=None,
                    duration_ms=duration_ms,
                    stderr=stderr_content,
                )
            else:
                error_msg = stderr_content or f"Exit code: {result.returncode}"
                logger.debug(f"Script failed: {error_msg}")
                return ExecutionResult(
                    success=False,
                    output=result.stdout.strip(),
                    error=error_msg,
                    duration_ms=duration_ms,
                    stderr=stderr_content,
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
