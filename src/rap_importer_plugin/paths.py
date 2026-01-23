"""Path expansion utilities for RAP Importer.

This module provides path expansion that supports:
- Environment variables: ${VAR} or $VAR
- Home directory: ~
- Both combined: ${RAP_BASE}/subfolder or ~/subfolder

The expand_path function chains os.path.expandvars() and os.path.expanduser()
to handle all cases. If an environment variable is not set, expandvars()
silently leaves it unchanged (e.g., "${UNDEFINED}" stays as-is), which allows
path validation to catch the issue with a clear error message.
"""

from __future__ import annotations

import os
from pathlib import Path


def expand_path(path: str) -> str:
    """Expand environment variables and ~ in a path string.

    Args:
        path: Path string that may contain ${VAR}, $VAR, or ~

    Returns:
        Expanded path string

    Examples:
        >>> expand_path("${HOME}/documents")  # Expands HOME env var
        >>> expand_path("~/documents")        # Expands ~
        >>> expand_path("${RAP_BASE}/import") # Expands RAP_BASE if set
    """
    # First expand ${VAR} and $VAR
    expanded = os.path.expandvars(path)
    # Then expand ~ to home directory
    expanded = os.path.expanduser(expanded)
    return expanded


def expand_path_to_path(path: str) -> Path:
    """Expand path and return as Path object.

    Args:
        path: Path string that may contain ${VAR}, $VAR, or ~

    Returns:
        Expanded Path object
    """
    return Path(expand_path(path))
