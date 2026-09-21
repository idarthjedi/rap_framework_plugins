"""Importer errors and their numeric codes.

Codes 1000-1008 are carried over from devonthink_importer.applescript so that
log searches and muscle memory keep working across both implementations.
"""

from __future__ import annotations

BAD_ARGUMENTS = 1000
NOT_IN_DATABASE_FOLDER = 1001
DATABASE_NOT_FOUND = 1002
NO_INCOMING_GROUP = 1003
NO_RECORD = 1006
HASH_FAILED = 1007
NOT_SEARCHABLE = 1008
# New with the MCP importer
MCP_FAILED = 1009
UNSAFE_TRASH = 1010  # refused to trash a record this run did not create
STAMP_FAILED = 1011
AMBIGUOUS_MATCH = 1012  # hash matches, but no record verifies as the same document
REPLICATE_FAILED = 1013


class ImporterError(Exception):
    """An import failure the pipeline should see as a failed script run."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code

    def __str__(self) -> str:
        return f"{super().__str__()} ({self.code})"
