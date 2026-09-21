"""Import one PDF into DEVONthink through the DEVONthink MCP server.

Port of scripts/devonthink_importer.applescript. Routing, duplicate detection,
the OCR decision and the error codes are unchanged. What changed is forced by
the MCP tools:

- OCR is two steps. `import_file` lands the file, then `ocr_record` produces a
  searchable copy as a NEW record and leaves the original in place. The
  pre-OCR original is trashed once the OCR'd copy exists.
- The sourcehash stamp goes on immediately after import, before OCR, so an
  interrupted run leaves a record the next run can find by hash. (The
  AppleScript stamped last, and needed an orphan scan to recover.)
- The MCP server gives DEVONthink 900 s per call, so the AppleScript's 120 s
  Apple Event ceiling -- and the -1712 handling built around it -- is gone.

Recognising a pre-OCR original relies on the sourcehash being computed on the
filesystem, outside DEVONthink: a record whose file still hashes to its
sourcehash is a byte-identical copy of the source that DEVONthink has not
transformed. That is the only kind of record this module will ever trash.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import errors
from .enrichment import enrich_record
from .errors import ImporterError
from .mcp_client import (
    HttpTransport,
    MCPClient,
    MCPError,
    MCPTimeout,
    MCPToolError,
    StdioTransport,
)

DEFAULT_OCR_TIMEOUT = 300.0
CALL_TIMEOUT = 120.0
SEARCH_TIMEOUT = 300.0

# Skip OCR only when a PDF's existing text layer is unambiguous. Set high on
# purpose: a needless OCR pass costs seconds, but a wrongly skipped one leaves
# a document unsearchable, which is the expensive mistake in a research corpus.
# Measured for reference: true scans read 0 chars/page, near-scans 13-41,
# ambiguous 89-91, born-digital papers 146-8139.
SKIP_OCR_ABOVE_CHARS_PER_PAGE = 1000

TextStats = Callable[[str], tuple[int, int, int]]


# ===================
# PATH PARSING
# ===================


@dataclass(frozen=True)
class PathComponents:
    """Where a file under the watch folder should land in DEVONthink."""

    database: str
    group_path: str
    is_inbox: bool
    is_root_level: bool


def parse_path_components(relative_path: str) -> PathComponents:
    """Route a watch-folder path to a database and group.

    - <db>/file.pdf                 -> the database's incoming group
    - <db>/Inbox/<groups>/file.pdf  -> <groups> inside the incoming group
    - <db>/<groups>/file.pdf        -> <groups> from the database root
    """
    parts = relative_path.split("/")
    if len(parts) < 2:
        raise ImporterError(
            f"File not in database subfolder: {relative_path}", errors.NOT_IN_DATABASE_FOLDER
        )

    database = parts[0]
    if len(parts) == 2:
        return PathComponents(database, "", is_inbox=False, is_root_level=True)

    # AppleScript's `is` compares case-insensitively, so the original matched
    # "inbox" and "INBOX" too. Keep that.
    if parts[1].casefold() == "inbox":
        return PathComponents(database, "/".join(parts[2:-1]), is_inbox=True, is_root_level=False)
    return PathComponents(database, "/".join(parts[1:-1]), is_inbox=False, is_root_level=False)


# ===================
# HASH AND OCR DECISION
# ===================


def sha256_file(path: str | Path) -> str:
    """SHA-256 of the file's raw bytes.

    Must stay byte-for-byte equivalent to `shasum -a 256` and to the
    research_analysis_platform's calculate_file_hash: RAP stamps the same value
    into Obsidian frontmatter, and the two are expected to agree.
    """
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                digest.update(chunk)
    except OSError as e:
        raise ImporterError(f"Failed to calculate file hash: {e}", errors.HASH_FAILED) from e
    return digest.hexdigest()


def needs_ocr(pages: int, chars_per_page: int) -> bool:
    """Decide whether a document should go to DEVONthink's OCR engine.

    OCR on a born-digital PDF is wasted work, and on some of them it is worse:
    DEVONthink's OCR engine wedges outright on documents that import instantly
    and already expose a full text layer.

    Calibration measured across this corpus (chars per page):
        0          true scans
        13 - 41    near-scans -- a little stray text, essentially images
        89 - 91    ambiguous -- thin or partial existing text layer
      146 - 8139   born-digital papers -- full text layer, OCR is pure cost

    Policy: OCR unless the text layer is unmistakably complete. An unparseable
    file (0 pages) cannot prove it has text, so it gets OCR'd.
    """
    if pages == 0:
        return True
    return chars_per_page < SKIP_OCR_ABOVE_CHARS_PER_PAGE


# ===================
# RECONCILING EXISTING RECORDS
# ===================


@dataclass(frozen=True)
class Hit:
    """An existing record carrying the incoming file's sourcehash."""

    uuid: str
    raw: bool  # file is byte-identical to the source: DEVONthink hasn't transformed it
    in_dest: bool  # already filed in the destination group
    added: str  # additionDate, ISO 8601


@dataclass(frozen=True)
class Plan:
    """What to do about existing hits.

    Exactly one of `keep` / `ocr` is set. When `ocr` is set, that raw record is
    OCR'd and the result is what gets kept. `status` is reported on stdout.
    """

    status: str  # "replicated" or "recovered"
    keep: str | None = None
    ocr: str | None = None
    trash: tuple[str, ...] = ()


def decide_existing(hits: list[Hit], ocr_wanted: bool) -> Plan:
    """Decide how to reconcile records that already carry this file's sourcehash.

    Raw hits are pre-OCR originals left behind by an interrupted run (or, when
    OCR is not wanted, ordinary finished imports of a born-digital file).
    Processed hits are records DEVONthink has transformed -- OCR'd, or
    annotated since.

        ocr_wanted  hits                action                               status
        ----------  ------------------  -----------------------------------  ----------
        yes         >= 1 processed      keep a processed hit, trash raws     recovered if anything
                                                                             is trashed, else
                                                                             replicated
        yes         raw only            OCR a raw hit, trash the raws        recovered
        no          any                 keep a hit, trash nothing            replicated

    The importer makes sure the kept record ends up in the destination group
    (replicating it there if needed), so the plan only picks records.

    Safety net: the applier refuses any plan that trashes a record whose `raw`
    is False, or the record being kept -- so a mistake here fails loudly
    instead of losing a document.
    """
    def best(candidates: list[Hit]) -> Hit:
        # Prefer a record already in the destination, so nothing needs
        # replicating; then the oldest, which is the one most likely to carry
        # annotations, x-devonthink-item links and Obsidian references.
        return min(candidates, key=lambda h: (not h.in_dest, h.added))

    if not ocr_wanted:
        # Raw hits are finished born-digital imports here, not leftovers.
        return Plan(status="replicated", keep=best(hits).uuid)

    raws = [h for h in hits if h.raw]
    processed = [h for h in hits if not h.raw]
    trash = tuple(h.uuid for h in raws)
    if processed:
        return Plan(
            status="recovered" if trash else "replicated",
            keep=best(processed).uuid,
            trash=trash,
        )
    # Every copy is raw: OCR one (they are byte-identical), then trash them all.
    return Plan(status="recovered", ocr=best(raws).uuid, trash=trash)


# ===================
# IMPORTER
# ===================


@dataclass(frozen=True)
class ImportResult:
    status: str  # success | imported | recovered | replicated
    uuid: str
    sourcehash: str


class DevonthinkImporter:
    """Runs one import against a connected MCP client."""

    def __init__(
        self,
        client: MCPClient,
        *,
        text_stats: TextStats,
        ocr_timeout: float = DEFAULT_OCR_TIMEOUT,
        enrich: bool = True,
        enrich_rename: bool = False,
        report: Callable[[str], None] = print,
        timing: Callable[[str, float], None] | None = None,
    ) -> None:
        self.client = client
        self.text_stats = text_stats
        self.ocr_timeout = ocr_timeout
        self.enrich = enrich
        self.enrich_rename = enrich_rename
        self.report = report
        self.timing = timing or (lambda op, secs: None)

    def run(self, file_path: Path, relative_path: str) -> ImportResult:
        components = parse_path_components(relative_path)
        database = self._database(components.database)
        db_uuid = database["uuid"]
        dest_uuid = self._destination(database, components)

        with self._timed("hash"):
            source_hash = sha256_file(file_path)
        pages, _, per_page = self.text_stats(str(file_path))
        ocr_wanted = needs_ocr(pages, per_page)

        with self._timed("search"):
            hits = self._find_hits(source_hash, db_uuid, dest_uuid)

        if hits:
            plan = decide_existing(hits, ocr_wanted)
            keeper, status, produced = self._apply(plan, hits, source_hash, db_uuid, dest_uuid)
            if produced:
                self._finish(keeper, source_hash, db_uuid, file_path)
            return ImportResult(status, keeper, source_hash)

        return self._import_new(file_path, source_hash, db_uuid, dest_uuid, ocr_wanted)

    # ---- lookups ----

    def _call(self, tool: str, timeout: float | None = None, **arguments: Any) -> Any:
        return self.client.call_tool(tool, arguments, timeout=timeout)

    def _database(self, name: str) -> dict[str, Any]:
        for db in self._call("get_databases"):
            if db.get("name") == name:
                return db
        raise ImporterError(
            f"Database not found: {name} (is it open, and not excluded from AI in "
            "DEVONthink's settings?)",
            errors.DATABASE_NOT_FOUND,
        )

    def _destination(self, database: dict[str, Any], components: PathComponents) -> str:
        db_uuid = database["uuid"]
        if components.is_inbox or components.is_root_level:
            inbox_uuid = database.get("incomingGroupUUID")
            if not inbox_uuid:
                raise ImporterError(
                    f"Database has no incoming group configured: {database['name']}",
                    errors.NO_INCOMING_GROUP,
                )
            if not components.group_path:
                return inbox_uuid
            if inbox_uuid == database.get("rootUUID"):
                location = f"/{components.group_path}"
            else:
                # Build from the incoming group's real name and place, not a
                # literal "Inbox", so a renamed inbox can't spawn a stray group.
                inbox = self._call("get_record_properties", uuid=inbox_uuid, database_uuid=db_uuid)
                base = inbox["location"].rstrip("/") + "/" + inbox["name"]
                location = f"{base}/{components.group_path}"
        else:
            if not components.group_path:
                return database["rootUUID"]
            location = f"/{components.group_path}"
        return self._call("create_group_path", location=location, database_uuid=db_uuid)["uuid"]

    def _find_hits(self, source_hash: str, db_uuid: str, dest_uuid: str) -> list[Hit]:
        # Trashed records are excluded by the search (verified), so a trashed
        # pre-OCR original can never be mistaken for a live duplicate.
        found = self._call(
            "search_records",
            timeout=SEARCH_TIMEOUT,
            query=f"mdsourcehash:{source_hash}",
            database_uuid=db_uuid,
            fields=["uuid", "additionDate"],
        )
        results = found.get("results", [])
        if not results:
            return []

        uuids = [r["uuid"] for r in results]
        paths = self._by_uuid(
            self._call("get_imported_record_path", uuids=uuids, database_uuid=db_uuid)
        )
        parents = self._by_uuid(
            self._call("get_record_parents", uuids=uuids, database_uuid=db_uuid)
        )
        return [
            Hit(
                uuid=r["uuid"],
                raw=self._is_raw(paths.get(r["uuid"], {}), source_hash),
                in_dest=any(p.get("uuid") == dest_uuid for p in parents.get(r["uuid"], {}).get("parents", [])),
                added=r.get("additionDate", ""),
            )
            for r in results
        ]

    @staticmethod
    def _by_uuid(batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {entry["uuid"]: entry for entry in batch.get("results", [])}

    @staticmethod
    def _is_raw(path_entry: dict[str, Any], source_hash: str) -> bool:
        # Indexed records point at files outside the database -- never treat
        # them as disposable, whatever their bytes.
        path = path_entry.get("path")
        if path_entry.get("indexed", True) or not path:
            return False
        try:
            return sha256_file(path) == source_hash
        except ImporterError:
            return False

    # ---- actions ----

    def _import_new(
        self, file_path: Path, source_hash: str, db_uuid: str, dest_uuid: str, ocr_wanted: bool
    ) -> ImportResult:
        with self._timed("import"):
            original = self._call(
                "import_file", path=str(file_path), database_uuid=db_uuid, destination=dest_uuid
            )["uuid"]
        self._stamp(original, source_hash)

        keeper, status = original, "imported"
        if ocr_wanted:
            with self._timed("ocr"):
                ocr_copy = self._ocr(original)
            if ocr_copy:
                self._trash_raw(original, source_hash, db_uuid)
                keeper, status = ocr_copy, "success"

        self._finish(keeper, source_hash, db_uuid, file_path)
        return ImportResult(status, keeper, source_hash)

    def _apply(
        self, plan: Plan, hits: list[Hit], source_hash: str, db_uuid: str, dest_uuid: str
    ) -> tuple[str, str, bool]:
        """Carry out a Plan. Returns (kept uuid, status, whether a new record was made)."""
        known = {h.uuid for h in hits}
        raw = {h.uuid for h in hits if h.raw}

        # Validate everything before touching anything.
        problems = []
        if (plan.keep is None) == (plan.ocr is None):
            problems.append("exactly one of keep/ocr must be set")
        if plan.keep is not None and plan.keep not in known:
            problems.append(f"keep {plan.keep} is not a hit")
        if plan.ocr is not None and plan.ocr not in raw:
            problems.append(f"ocr target {plan.ocr} is not a raw hit")
        if plan.keep in plan.trash:
            problems.append(f"would trash the record it keeps ({plan.keep})")
        if unsafe := [u for u in plan.trash if u not in raw]:
            problems.append(f"would trash non-raw records {unsafe}")
        if problems:
            raise ImporterError(f"Refusing plan {plan}: {'; '.join(problems)}", errors.UNSAFE_PLAN)

        keeper, status, produced = plan.keep, plan.status, False
        if plan.ocr is not None:
            with self._timed("ocr"):
                ocr_copy = self._ocr(plan.ocr)
            if ocr_copy:
                keeper, produced = ocr_copy, True
            else:
                keeper, status = plan.ocr, "imported"
        assert keeper is not None

        for uuid in plan.trash:
            if uuid != keeper:
                self._trash_raw(uuid, source_hash, db_uuid)

        with self._timed("replicate"):
            self._ensure_in_destination(keeper, db_uuid, dest_uuid)
        return keeper, status, produced

    def _ensure_in_destination(self, uuid: str, db_uuid: str, dest_uuid: str) -> None:
        parents = self._by_uuid(self._call("get_record_parents", uuids=[uuid], database_uuid=db_uuid))
        if any(p.get("uuid") == dest_uuid for p in parents.get(uuid, {}).get("parents", [])):
            return
        self._call("replicate_record", uuid=uuid, destination=dest_uuid, database_uuid=db_uuid)

    def _stamp(self, uuid: str, source_hash: str) -> None:
        # mode="merge" is essential: the default REPLACES all custom metadata.
        result = self._call(
            "set_record_custom_metadata",
            uuid=uuid,
            metadata={"sourcehash": source_hash},
            mode="merge",
        )
        if result.get("dropped_fields") or result.get("metadata", {}).get("sourcehash") != source_hash:
            raise ImporterError(f"Could not stamp sourcehash on {uuid}: {result}", errors.STAMP_FAILED)

    def _ocr(self, uuid: str) -> str | None:
        """OCR a record. Returns the new record's uuid, or None to keep the original."""
        try:
            return self._call("ocr_record", timeout=self.ocr_timeout, uuid=uuid)["uuid"]
        except MCPTimeout:
            # DEVONthink keeps working after we stop waiting, so an OCR'd copy
            # may still land beside the original. The next run that sees this
            # file finds both by hash and tidies up.
            self.report(
                f"WARNING: OCR did not finish within {self.ocr_timeout:.0f}s; "
                "keeping the document without OCR"
            )
        except MCPToolError as e:
            self.report(f"WARNING: OCR failed ({e}); keeping the document without OCR")
        return None

    def _trash_raw(self, uuid: str, source_hash: str, db_uuid: str) -> None:
        """Trash a record, but only if it is still a byte-identical copy of the source."""
        entry = self._by_uuid(
            self._call("get_imported_record_path", uuids=[uuid], database_uuid=db_uuid)
        ).get(uuid, {})
        if not self._is_raw(entry, source_hash):
            self.report(f"WARNING: not trashing {uuid}: it no longer matches the source file")
            return
        self._call("trash_record", uuid=uuid, database_uuid=db_uuid)

    def _finish(self, uuid: str, source_hash: str, db_uuid: str, file_path: Path) -> None:
        """Enrich a newly made record, then confirm it is stamped and searchable."""
        if self.enrich:
            with self._timed("enrich"):
                enrich_record(
                    self.client, uuid, db_uuid, rename=self.enrich_rename, report=self.report
                )

        # Enrichment was verified to merge, but a lost stamp would silently make
        # every future import of this file a duplicate, so check anyway.
        metadata = self._call("get_record_custom_metadata", uuid=uuid, database_uuid=db_uuid)
        if metadata.get("sourcehash") != source_hash:
            self.report("WARNING: sourcehash missing after enrichment; re-stamping")
            self._stamp(uuid, source_hash)

        # A record with no words is filed but invisible to search. It looks
        # identical to a healthy import in the UI, so nothing else would ever
        # surface it.
        props = self._call("get_record_properties", uuid=uuid, database_uuid=db_uuid)
        if not props.get("wordCount"):
            raise ImporterError(
                f"Imported but NOT searchable (no text layer, OCR unavailable): {file_path}",
                errors.NOT_SEARCHABLE,
            )

    @contextmanager
    def _timed(self, operation: str) -> Iterator[None]:
        start = time.monotonic()
        try:
            yield
        finally:
            self.timing(operation, time.monotonic() - start)


# ===================
# COMMAND LINE
# ===================


def main(argv: list[str] | None = None, *, text_stats: TextStats) -> int:
    """Entry point for scripts/devonthink_importer.py.

    stdout: WARNING lines, then the outcome (success | imported | recovered |
    replicated), then `sourcehash=<hex>`. The pipeline logs every stdout line.
    stderr: `TIMING:` lines, which the pipeline logs, and on failure the error.
    Exit status is non-zero on failure, which stops the rest of the pipeline.
    """
    parser = argparse.ArgumentParser(
        prog="devonthink_importer.py",
        description="Import a PDF into DEVONthink via the DEVONthink MCP server.",
    )
    parser.add_argument("file_path", type=Path, help="full path to the PDF")
    parser.add_argument("relative_path", help="path relative to the watch folder")
    parser.add_argument(
        "ocr_timeout", nargs="?", type=float, default=DEFAULT_OCR_TIMEOUT,
        help=f"seconds to wait for OCR (default {DEFAULT_OCR_TIMEOUT:.0f})",
    )
    parser.add_argument(
        "--transport", choices=["stdio", "http"],
        default=os.environ.get("DEVONTHINK_MCP_TRANSPORT", "stdio"),
        help="how to reach the MCP server (default stdio)",
    )
    parser.add_argument(
        "--no-enrich", dest="enrich", action="store_false",
        help="skip DOI/ISBN bibliographic enrichment",
    )
    parser.add_argument(
        "--enrich-rename", action="store_true",
        help="let enrichment rename records to the paper or book title",
    )
    args = parser.parse_args(argv)

    def timing(operation: str, seconds: float) -> None:
        print(f"TIMING: {operation}={seconds:.3f}s", file=sys.stderr)

    transport = HttpTransport() if args.transport == "http" else StdioTransport()
    try:
        with MCPClient(transport, timeout=CALL_TIMEOUT) as client:
            importer = DevonthinkImporter(
                client,
                text_stats=text_stats,
                ocr_timeout=args.ocr_timeout,
                enrich=args.enrich,
                enrich_rename=args.enrich_rename,
                timing=timing,
            )
            result = importer.run(args.file_path, args.relative_path)
    except ImporterError as e:
        print(str(e), file=sys.stderr)
        return 1
    except MCPError as e:
        print(f"DEVONthink MCP error: {e} ({errors.MCP_FAILED})", file=sys.stderr)
        return 1

    print(result.status)
    print(f"sourcehash={result.sourcehash}")
    return 0
