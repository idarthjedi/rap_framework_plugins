"""Import one PDF into DEVONthink through the DEVONthink MCP server.

Port of scripts/devonthink_importer.applescript. Routing, duplicate detection,
the OCR decision and the error codes carry over. What changed is forced by the
MCP tools:

- OCR is two steps. `import_file` lands the file, then `ocr_record` produces a
  searchable copy as a NEW record and leaves the original in place. The
  pre-OCR original -- created seconds earlier by the same run -- is trashed
  once the OCR'd copy is verified.
- The sourcehash stamp goes on immediately after import, before OCR.
- The MCP server gives DEVONthink 900 s per call, so the AppleScript's 120 s
  Apple Event ceiling -- and the -1712 handling built around it -- is gone.

Two rules keep existing documents safe:

- A record this run did not create is never OCR'd, modified or trashed. When
  the incoming file matches an existing record by hash, the record is only
  replicated into the destination group -- and only once it is verified to be
  the same document. An unverifiable match fails the run instead.
- DEVONthink records are only ever examined through the MCP server. Nothing
  here reads or writes files inside a DEVONthink database.

The runner surfaces only pass or fail (a failure notification, with the file
left in the import folder). So there are no warnings: anything that could
lose content or file a document against the wrong record fails the run.
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
from .pdf_info import PdfInfo, pdf_info

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
InspectPdf = Callable[[str | Path], PdfInfo]


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
# MATCHING EXISTING RECORDS
# ===================


@dataclass(frozen=True)
class Hit:
    """An existing record whose sourcehash search matched the incoming file."""

    uuid: str
    name: str
    in_dest: bool  # already filed in the destination group
    added: str  # additionDate, ISO 8601
    problem: str = ""  # why it is not verified as the same document; "" when it is

    @property
    def verified(self) -> bool:
        return not self.problem


def identity_problem(props: dict[str, Any], source_hash: str, pages: int) -> str:
    """Why a hash-matched record is not verifiably the incoming document, or "".

    Judged from the record's DEVONthink properties alone, never its file:
    - its stored sourcehash equals the incoming hash exactly, rather than
      trusting however the search operator matched;
    - it is a PDF -- a converted or derived record can inherit the stamp
      without being the document;
    - the page counts agree. OCR and annotation never change a page count; a
      merged, split or replaced document does.
    """
    stamp = (props.get("customMetadata") or {}).get("sourcehash")
    if stamp != source_hash:
        return f"its stored sourcehash differs ({str(stamp)[:12]}...)"
    if props.get("type") != "pdf":
        return f"it is a {props.get('kind') or props.get('type')}, not a PDF"
    record_pages = props.get("pageCount") or 0
    if not pages:
        return "this file's page count could not be read"
    if not record_pages:
        return "its page count is unknown"
    if record_pages != pages:
        return f"its page count differs ({record_pages} vs {pages})"
    return ""


def choose_existing(hits: list[Hit]) -> Hit | None:
    """Pick the record to file this document as, or None when none verifies.

    Prefer a record already in the destination group, so nothing needs
    replicating; then the oldest, which is the one most likely to carry
    annotations, x-devonthink-item links and Obsidian references.
    """
    verified = [h for h in hits if h.verified]
    if not verified:
        return None
    return min(verified, key=lambda h: (not h.in_dest, h.added))


# ===================
# IMPORTER
# ===================


@dataclass(frozen=True)
class ImportResult:
    status: str  # success | imported | replicated
    uuid: str
    sourcehash: str
    name: str
    location: str  # the destination group, as a DEVONthink location path


def result_lines(result: ImportResult) -> list[str]:
    """The stdout lines the pipeline logs for a successful run."""
    return [
        result.status,
        f"record={result.uuid}",
        f'name="{result.name}" location={result.location}',
        f"sourcehash={result.sourcehash}",
    ]


class DevonthinkImporter:
    """Runs one import against a connected MCP client."""

    def __init__(
        self,
        client: MCPClient,
        *,
        text_stats: TextStats,
        inspect_pdf: InspectPdf = pdf_info,
        ocr_timeout: float = DEFAULT_OCR_TIMEOUT,
        enrich: bool = True,
        enrich_rename: bool = False,
        report: Callable[[str], None] = print,
        timing: Callable[[str, float], None] | None = None,
    ) -> None:
        self.client = client
        self.text_stats = text_stats
        self.inspect_pdf = inspect_pdf
        self.ocr_timeout = ocr_timeout
        self.enrich = enrich
        self.enrich_rename = enrich_rename
        self.report = report
        self.timing = timing or (lambda op, secs: None)
        # The only records this run may ever trash.
        self._created_this_run: set[str] = set()

    def run(self, file_path: Path, relative_path: str) -> ImportResult:
        components = parse_path_components(relative_path)
        database = self._database(components.database)
        db_uuid = database["uuid"]
        dest_uuid, dest_path = self._destination(database, components)

        with self._timed("hash"):
            source_hash = sha256_file(file_path)
        info = self.inspect_pdf(file_path)
        stats_pages, chars, _ = self.text_stats(str(file_path))
        pages = info.pages or stats_pages
        ocr_wanted = needs_ocr(pages, chars // pages if pages else 0)

        with self._timed("search"):
            hits = self._find_hits(source_hash, db_uuid, dest_uuid, pages)
        if hits:
            return self._file_existing(file_path, hits, source_hash, db_uuid, dest_uuid, dest_path)
        return self._import_new(
            file_path, source_hash, db_uuid, dest_uuid, dest_path, ocr_wanted, info.encrypted
        )

    # ---- lookups ----

    def _call(self, tool: str, timeout: float | None = None, **arguments: Any) -> Any:
        return self.client.call_tool(tool, arguments, timeout=timeout)

    @staticmethod
    def _by_uuid(batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {entry["uuid"]: entry for entry in batch.get("results", [])}

    def _database(self, name: str) -> dict[str, Any]:
        for db in self._call("get_databases"):
            if db.get("name") == name:
                return db
        raise ImporterError(
            f"Database not found: {name} (is it open, and not excluded from AI in "
            "DEVONthink's settings?)",
            errors.DATABASE_NOT_FOUND,
        )

    def _destination(self, database: dict[str, Any], components: PathComponents) -> tuple[str, str]:
        """Return the destination group's uuid and its location path."""
        db_uuid = database["uuid"]
        if components.is_inbox or components.is_root_level:
            inbox_uuid = database.get("incomingGroupUUID")
            if not inbox_uuid:
                raise ImporterError(
                    f"Database has no incoming group configured: {database['name']}",
                    errors.NO_INCOMING_GROUP,
                )
            if inbox_uuid == database.get("rootUUID"):
                base = ""
            else:
                # Build from the incoming group's real name and place, not a
                # literal "Inbox", so a renamed inbox can't spawn a stray group.
                inbox = self._call("get_record_properties", uuid=inbox_uuid, database_uuid=db_uuid)
                base = inbox["location"].rstrip("/") + "/" + inbox["name"]
            if not components.group_path:
                return inbox_uuid, f"{base}/"
            location = f"{base}/{components.group_path}"
        else:
            if not components.group_path:
                return database["rootUUID"], "/"
            location = f"/{components.group_path}"
        group = self._call("create_group_path", location=location, database_uuid=db_uuid)
        return group["uuid"], f"{location}/"

    def _find_hits(self, source_hash: str, db_uuid: str, dest_uuid: str, pages: int) -> list[Hit]:
        # Trashed records are excluded by the search, so a trashed record can
        # never be mistaken for a live duplicate.
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
        props = self._by_uuid(self._call("get_record_properties", uuids=uuids, database_uuid=db_uuid))
        parents = self._by_uuid(self._call("get_record_parents", uuids=uuids, database_uuid=db_uuid))
        return [
            Hit(
                uuid=r["uuid"],
                name=props.get(r["uuid"], {}).get("name", r["uuid"]),
                in_dest=self._has_parent(parents.get(r["uuid"], {}), dest_uuid),
                added=r.get("additionDate", ""),
                problem=identity_problem(props.get(r["uuid"], {}), source_hash, pages),
            )
            for r in results
        ]

    @staticmethod
    def _has_parent(parents_entry: dict[str, Any], group_uuid: str) -> bool:
        return any(p.get("uuid") == group_uuid for p in parents_entry.get("parents", []))

    # ---- filing ----

    def _file_existing(
        self,
        file_path: Path,
        hits: list[Hit],
        source_hash: str,
        db_uuid: str,
        dest_uuid: str,
        dest_path: str,
    ) -> ImportResult:
        """File the document as an existing record. Never modifies that record."""
        chosen = choose_existing(hits)
        if chosen is None:
            first = hits[0]
            more = f" (and {len(hits) - 1} more)" if len(hits) > 1 else ""
            raise ImporterError(
                f"{file_path.name} matches '{first.name}'{more} by hash but "
                f"{first.problem} -- not filed",
                errors.AMBIGUOUS_MATCH,
            )
        with self._timed("replicate"):
            self._ensure_in_destination(chosen, db_uuid, dest_uuid)
        return ImportResult("replicated", chosen.uuid, source_hash, chosen.name, dest_path)

    def _ensure_in_destination(self, hit: Hit, db_uuid: str, dest_uuid: str) -> None:
        if hit.in_dest:
            return
        self._call("replicate_record", uuid=hit.uuid, destination=dest_uuid, database_uuid=db_uuid)
        parents = self._by_uuid(self._call("get_record_parents", uuids=[hit.uuid], database_uuid=db_uuid))
        if not self._has_parent(parents.get(hit.uuid, {}), dest_uuid):
            raise ImporterError(
                f"Could not file '{hit.name}' into its destination group", errors.REPLICATE_FAILED
            )

    def _import_new(
        self,
        file_path: Path,
        source_hash: str,
        db_uuid: str,
        dest_uuid: str,
        dest_path: str,
        ocr_wanted: bool,
        encrypted: bool,
    ) -> ImportResult:
        with self._timed("import"):
            original = self._call(
                "import_file", path=str(file_path), database_uuid=db_uuid, destination=dest_uuid
            )["uuid"]
        self._created_this_run.add(original)
        self._stamp(original, source_hash)

        keeper, status = original, "imported"
        if encrypted:
            # OCR always fails on these, and they carry a text layer anyway.
            self.report("Password-protected PDF; OCR skipped")
        elif ocr_wanted:
            with self._timed("ocr"):
                ocr_copy = self._ocr(original)
            if ocr_copy and self._is_verified_copy(ocr_copy, original, source_hash, db_uuid):
                self._trash_created(original, db_uuid)
                keeper, status = ocr_copy, "success"

        name = self._finish(keeper, source_hash, db_uuid, file_path)
        return ImportResult(status, keeper, source_hash, name, dest_path)

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
        """OCR a record this run created. Returns the copy's uuid, or None to keep the original."""
        try:
            return self._call("ocr_record", timeout=self.ocr_timeout, uuid=uuid)["uuid"]
        except MCPTimeout:
            self.report(f"OCR did not finish within {self.ocr_timeout:.0f}s; kept without OCR")
        except MCPToolError as e:
            self.report(f"OCR failed ({e}); kept without OCR")
        return None

    def _is_verified_copy(self, ocr_copy: str, original: str, source_hash: str, db_uuid: str) -> bool:
        """True when OCR produced a distinct record that carries the stamp.

        Guards against trashing the only copy: if ocr_record ever handed back
        the original, or a copy we cannot confirm, the original is kept.
        """
        if ocr_copy == original:
            self.report("OCR returned the original record, not a copy; kept without OCR")
            return False
        try:
            metadata = self._call("get_record_custom_metadata", uuid=ocr_copy, database_uuid=db_uuid)
        except MCPToolError:
            metadata = {}
        if metadata.get("sourcehash") != source_hash:
            self.report(f"OCR copy {ocr_copy} could not be verified; kept the original")
            return False
        self._created_this_run.add(ocr_copy)
        return True

    def _trash_created(self, uuid: str, db_uuid: str) -> None:
        """Trash a record this run created. Any other record is refused outright."""
        if uuid not in self._created_this_run:
            raise ImporterError(
                f"Refusing to trash {uuid}: this run did not create it", errors.UNSAFE_TRASH
            )
        self._call("trash_record", uuid=uuid, database_uuid=db_uuid)

    def _finish(self, uuid: str, source_hash: str, db_uuid: str, file_path: Path) -> str:
        """Enrich a record this run made, confirm it is stamped and searchable, return its name."""
        if self.enrich:
            with self._timed("enrich"):
                enrich_record(
                    self.client, uuid, db_uuid, rename=self.enrich_rename, report=self.report
                )

        # Enrichment was verified to merge, but a lost stamp would silently make
        # every future import of this file a duplicate, so check anyway.
        metadata = self._call("get_record_custom_metadata", uuid=uuid, database_uuid=db_uuid)
        if metadata.get("sourcehash") != source_hash:
            self.report("sourcehash missing after enrichment; re-stamped")
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
        return props.get("name", "")

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

    stdout: progress notes, then the outcome (success | imported | replicated),
    `record=`, `name=`/`location=` and `sourcehash=`. The pipeline logs every
    stdout line; none of it is shown to the user.
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

    # Held back until the outcome is known. On failure the pipeline turns all of
    # stderr into the "Import Failed" notification, and a banner shows only its
    # first line -- so the reason must come before any TIMING lines. The
    # pipeline picks TIMING lines out of stderr wherever they appear.
    timings: list[str] = []

    def timing(operation: str, seconds: float) -> None:
        timings.append(f"TIMING: {operation}={seconds:.3f}s")

    failure = None
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
        failure = str(e)
    except MCPError as e:
        failure = f"DEVONthink MCP error: {e} ({errors.MCP_FAILED})"

    if failure is not None:
        print(failure, file=sys.stderr)
    for line in timings:
        print(line, file=sys.stderr)
    if failure is not None:
        return 1
    for line in result_lines(result):
        print(line)
    return 0
