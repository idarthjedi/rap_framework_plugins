"""Read-only audit: is each PDF in a folder filed in DEVONthink?

The importer runs under a menu-bar app that only reports pass or fail. This is
the on-demand view of everything else. Per file it reports one of:

    found      a live record carries its hash and passes the identity check
    duplicate  more than one live record does
    ambiguous  a live record carries its hash but fails the identity check
    trashed    only records in the database's trash carry its hash
    missing    nothing in DEVONthink carries its hash
    skipped    the file is not under a database folder, or its database is not open

"Identity check" is the importer's own identity_problem(), so "found" means
exactly what a re-import would accept.

The audit can only look. ReadOnlyClient refuses every tool not on an
allowlist of read-only ones, before the request is sent. It reads the files
being audited and nothing inside a DEVONthink database.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import ImporterError
from .importer import TextStats, identity_problem, parse_path_components, sha256_file
from .mcp_client import MCPClient, MCPError, StdioTransport
from .pdf_info import pdf_info

READ_ONLY_TOOLS = frozenset(
    {
        "get_databases",
        "search_records",
        "get_record_properties",
        "get_record_children",
    }
)
BATCH = 500  # the MCP server's limit for uuids per call
PAGE = 1000  # its limit for children per call

SEVERITY = ["missing", "trashed", "ambiguous", "duplicate", "skipped", "found"]


class ReadOnlyClient:
    """Wraps an MCP client so only read-only tools can be called."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        if name not in READ_ONLY_TOOLS:
            raise PermissionError(f"The audit is read-only; refusing to call {name}")
        return self._client.call_tool(name, arguments, timeout=timeout)


@dataclass
class AuditRow:
    file: str  # relative to the audited folder
    status: str
    records: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""


def _summary(props: dict[str, Any]) -> dict[str, Any]:
    return {k: props.get(k) for k in ("name", "uuid", "location", "kind", "wordCount")}


class Auditor:
    """Audits files against DEVONthink through a read-only MCP client."""

    def __init__(
        self,
        client: Any,
        *,
        text_stats: TextStats | None = None,
        inspect_pdf: Callable[[str | Path], Any] = pdf_info,
    ) -> None:
        self.client = ReadOnlyClient(client)
        self.text_stats = text_stats
        self.inspect_pdf = inspect_pdf
        self._databases: dict[str, dict[str, Any]] | None = None
        self._trash_by_hash: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def audit(self, path: Path, root: Path) -> AuditRow:
        relative = path.relative_to(root).as_posix()
        try:
            components = parse_path_components(relative)
        except ImporterError:
            return AuditRow(relative, "skipped", detail="not inside a database folder")
        database = self._database(components.database)
        if database is None:
            return AuditRow(relative, "skipped", detail=f"database '{components.database}' is not open")

        source_hash = sha256_file(path)
        pages = self._pages(path)
        db_uuid = database["uuid"]

        found = self.client.call_tool(
            "search_records",
            {"query": f"mdsourcehash:{source_hash}", "database_uuid": db_uuid, "fields": ["uuid"]},
        )
        uuids = [r["uuid"] for r in found.get("results", [])]
        if uuids:
            props = self._properties(uuids, db_uuid)
            verified = [p for p in props if not identity_problem(p, source_hash, pages)]
            if verified:
                return AuditRow(
                    relative,
                    "duplicate" if len(verified) > 1 else "found",
                    [_summary(p) for p in verified],
                )
            return AuditRow(
                relative,
                "ambiguous",
                [_summary(props[0])],
                identity_problem(props[0], source_hash, pages),
            )

        trashed = self._trash(database).get(source_hash, [])
        if trashed:
            return AuditRow(relative, "trashed", [_summary(p) for p in trashed])
        return AuditRow(relative, "missing")

    def _pages(self, path: Path) -> int:
        pages = self.inspect_pdf(path).pages
        if not pages and self.text_stats:
            pages = self.text_stats(str(path))[0]
        return pages

    def _database(self, name: str) -> dict[str, Any] | None:
        if self._databases is None:
            self._databases = {db["name"]: db for db in self.client.call_tool("get_databases")}
        return self._databases.get(name)

    def _properties(self, uuids: list[str], db_uuid: str) -> list[dict[str, Any]]:
        props: list[dict[str, Any]] = []
        for start in range(0, len(uuids), BATCH):
            batch = self.client.call_tool(
                "get_record_properties", {"uuids": uuids[start : start + BATCH], "database_uuid": db_uuid}
            )
            props.extend(batch.get("results", []))
        return props

    def _trash(self, database: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """Index the database's trash by sourcehash, once per database.

        The hash search never sees trashed records, even scoped to the trash
        group, so the trash is listed and its records' stamps read instead.
        """
        db_uuid = database["uuid"]
        if db_uuid not in self._trash_by_hash:
            index: dict[str, list[dict[str, Any]]] = {}
            uuids = list(self._walk(database["trashGroupUUID"], depth=0))
            for props in self._properties(uuids, db_uuid):
                stamp = (props.get("customMetadata") or {}).get("sourcehash")
                if stamp:
                    index.setdefault(stamp, []).append(props)
            self._trash_by_hash[db_uuid] = index
        return self._trash_by_hash[db_uuid]

    def _walk(self, group_uuid: str, depth: int) -> Iterator[str]:
        """Yield the uuids of documents under a group, descending into subgroups."""
        offset = 0
        while True:
            page = self.client.call_tool(
                "get_record_children", {"uuid": group_uuid, "offset": offset, "limit": PAGE}
            )
            for item in page.get("items", []):
                if item.get("type") == "group":
                    if depth < 10:
                        yield from self._walk(item["uuid"], depth + 1)
                else:
                    yield item["uuid"]
            offset += page.get("count", 0)
            if not page.get("count") or offset >= page.get("total", 0):
                return


def select_files(folder: Path, since: datetime | None) -> list[Path]:
    """PDFs under a folder, optionally only those moved there on or after a date.

    Uses inode change time: moving a file into the folder updates it, while the
    file's modified date is preserved by the move.
    """
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    if since is not None:
        cutoff = since.timestamp()
        files = [p for p in files if p.stat().st_ctime >= cutoff]
    return files


def format_row(row: AuditRow) -> str:
    label = row.status.upper().ljust(9)
    if not row.records:
        return f"{label}  {row.file}" + (f"  -- {row.detail}" if row.detail else "")
    described = "; ".join(
        f"\"{r['name']}\" {r['location']} ({str(r['uuid'])[:8]}, {r['kind']}, {r['wordCount']} words)"
        for r in row.records
    )
    return f"{label}  {row.file}  ->  {described}" + (f"  -- {row.detail}" if row.detail else "")


def main(argv: list[str] | None = None, *, text_stats: TextStats | None = None, env_file: Path | None = None) -> int:
    """Entry point for scripts/audit_imports.py. Exit 0 when every file is found."""
    if env_file is not None and env_file.is_file():
        from dotenv import load_dotenv

        load_dotenv(env_file)
    rap_base = os.environ.get("RAP_BASE")
    default_folder = Path(rap_base).expanduser() / "RAPPlatform-Import" / "_Archived" if rap_base else None

    parser = argparse.ArgumentParser(
        prog="audit_imports.py",
        description="Read-only: report which PDFs in a folder are filed in DEVONthink.",
    )
    parser.add_argument(
        "folder", nargs="?", type=Path, default=default_folder,
        help="folder whose subfolders are DEVONthink database names "
        "(default: $RAP_BASE/RAPPlatform-Import/_Archived)",
    )
    parser.add_argument(
        "--since", type=datetime.fromisoformat,
        help="only files moved into the folder on or after this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--problems-only", action="store_true", help="omit files that were found"
    )
    args = parser.parse_args(argv)
    if args.folder is None:
        parser.error("no folder given and RAP_BASE is not set")
    folder = args.folder.expanduser()
    if not folder.is_dir():
        parser.error(f"not a folder: {folder}")

    files = select_files(folder, args.since)
    try:
        with MCPClient(StdioTransport()) as client:
            auditor = Auditor(client, text_stats=text_stats)
            rows = [auditor.audit(path, folder) for path in files]
    except MCPError as e:
        print(f"DEVONthink MCP error: {e}", file=sys.stderr)
        return 1

    rows.sort(key=lambda r: (SEVERITY.index(r.status), r.file))
    for row in rows:
        if not (args.problems_only and row.status == "found"):
            print(format_row(row))
    counts = Counter(r.status for r in rows)
    print(f"\n{len(rows)} files: " + ", ".join(f"{counts[s]} {s}" for s in SEVERITY if counts[s]))
    return 0 if counts["found"] == len(rows) else 1
