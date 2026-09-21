"""Tests for the MCP-based DEVONthink importer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from rap_importer_plugin.devonthink import errors
from rap_importer_plugin.devonthink.enrichment import enrich_record, looks_like_isbn
from rap_importer_plugin.devonthink.errors import ImporterError
from rap_importer_plugin.devonthink.importer import (
    DevonthinkImporter,
    Hit,
    Plan,
    decide_existing,
    needs_ocr,
    parse_path_components,
    sha256_file,
)
from rap_importer_plugin.devonthink.mcp_client import MCPTimeout, MCPToolError

RAP_PROJECT = Path.home() / "development/anthropics/projects/research_analysis_platform"

SCANNED = (1, 0, 0)  # pdf_text_stats output for an image-only page
BORN_DIGITAL = (2, 10044, 5022)


class FakeDevonthink:
    """In-memory stand-in for the DEVONthink MCP tools the importer calls.

    Mirrors behaviour measured against the real server: ocr_record makes a NEW
    record (different bytes, custom metadata copied) and leaves the original;
    trashed records drop out of search; an un-OCR'd image PDF still reports a
    few words from DEVONthink's light text recognition.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.store = tmp_path / "Files.noindex"
        self.store.mkdir()
        self.database = {
            "uuid": "DB",
            "name": "Liberty.University",
            "rootUUID": "DB",
            "incomingGroupUUID": "INBOX",
        }
        self.groups: dict[str, str] = {"/": "DB", "/Inbox": "INBOX"}  # location -> uuid
        self.group_props = {"INBOX": {"location": "/", "name": "Inbox"}}
        self.records: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.ocr_behaviour = "ok"
        self.raw_word_count = 4
        self._next = 0

    # -- helpers for arranging state --

    def add_record(
        self, source: Path, group: str, *, stamp: str | None, processed: bool, added: str = "2026-01-01"
    ) -> str:
        uuid = self._uuid()
        path = self.store / f"{uuid}.pdf"
        data = source.read_bytes() + (b"%OCR-TEXT-LAYER" if processed else b"")
        path.write_bytes(data)
        self.records[uuid] = {
            "path": path,
            "parents": {group},
            "meta": {"sourcehash": stamp} if stamp else {},
            "words": 18 if processed else self.raw_word_count,
            "trashed": False,
            "added": added,
        }
        return uuid

    def live(self) -> dict[str, dict[str, Any]]:
        return {u: r for u, r in self.records.items() if not r["trashed"]}

    def called(self, tool: str) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == tool]

    def _uuid(self) -> str:
        self._next += 1
        return f"REC{self._next}"

    # -- the MCP surface --

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        args = arguments or {}
        self.calls.append((name, args))
        return getattr(self, f"_{name}")(**args)

    def _get_databases(self) -> list[dict[str, Any]]:
        return [self.database]

    def _create_group_path(self, location: str, database_uuid: str) -> dict[str, Any]:
        key = "/" + location.strip("/")
        self.groups.setdefault(key, f"GRP{len(self.groups)}")
        return {"uuid": self.groups[key]}

    def _get_record_properties(self, uuid: str, database_uuid: str | None = None) -> dict[str, Any]:
        if uuid in self.group_props:
            return self.group_props[uuid]
        rec = self.records[uuid]
        props = {"uuid": uuid, "wordCount": rec["words"], "customMetadata": dict(rec["meta"])}
        if "doi" in rec:
            props["doi"] = rec["doi"]
        return props

    def _import_file(self, path: str, database_uuid: str, destination: str) -> dict[str, Any]:
        uuid = self._uuid()
        copy = self.store / f"{uuid}.pdf"
        shutil.copyfile(path, copy)
        self.records[uuid] = {
            "path": copy, "parents": {destination}, "meta": {},
            "words": self.raw_word_count, "trashed": False, "added": "2026-09-21",
        }
        return {"uuid": uuid}

    def _set_record_custom_metadata(self, uuid: str, metadata: dict[str, Any], mode: str = "replace") -> dict[str, Any]:
        rec = self.records[uuid]
        rec["meta"] = {**rec["meta"], **metadata} if mode == "merge" else dict(metadata)
        return {"uuid": uuid, "metadata": dict(rec["meta"]), "dropped_fields": []}

    def _get_record_custom_metadata(self, uuid: str, database_uuid: str | None = None) -> dict[str, Any]:
        return dict(self.records[uuid]["meta"])

    def _search_records(self, query: str, database_uuid: str, fields: list[str]) -> dict[str, Any]:
        wanted = query.removeprefix("mdsourcehash:")
        hits = [
            {"uuid": u, "additionDate": r["added"]}
            for u, r in self.live().items()
            if r["meta"].get("sourcehash") == wanted
        ]
        return {"results": hits, "total": len(hits)}

    def _get_imported_record_path(self, uuids: list[str], database_uuid: str) -> dict[str, Any]:
        return {"results": [
            {"uuid": u, "path": str(self.records[u]["path"]), "indexed": False} for u in uuids
        ]}

    def _get_record_parents(self, uuids: list[str], database_uuid: str) -> dict[str, Any]:
        return {"results": [
            {"uuid": u, "parents": [{"uuid": g} for g in self.records[u]["parents"]]} for u in uuids
        ]}

    def _ocr_record(self, uuid: str) -> dict[str, Any]:
        if self.ocr_behaviour == "timeout":
            raise MCPTimeout("No reply within 300s")
        if self.ocr_behaviour == "error":
            raise MCPToolError("ocr_record: OCR returned no result")
        original = self.records[uuid]
        new = self._uuid()
        path = self.store / f"{new}.pdf"
        path.write_bytes(original["path"].read_bytes() + b"%OCR-TEXT-LAYER")
        self.records[new] = {
            "path": path, "parents": set(original["parents"]), "meta": dict(original["meta"]),
            "words": 18, "trashed": False, "added": "2026-09-21",
        }
        return {"uuid": new}

    def _trash_record(self, uuid: str, database_uuid: str) -> str:
        self.records[uuid]["trashed"] = True
        return "Record moved to trash"

    def _replicate_record(self, uuid: str, destination: str, database_uuid: str) -> dict[str, Any]:
        self.records[uuid]["parents"].add(destination)
        return {"uuid": uuid, "destination_uuid": destination}

    def _resolve_doi_metadata(self, uuid: str, database_uuid: str, doi: str, rename: bool, **_: Any) -> dict[str, Any]:
        self.records[uuid]["meta"].update({"doi": doi, "journal": "Personnel Psychology"})
        return {"record_enriched": True}


@pytest.fixture
def dt(tmp_path: Path) -> FakeDevonthink:
    return FakeDevonthink(tmp_path)


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4 source bytes of the incoming paper")
    return source


def make_importer(dt: FakeDevonthink, stats: tuple[int, int, int], **kwargs: Any) -> tuple[DevonthinkImporter, list[str]]:
    reported: list[str] = []
    importer = DevonthinkImporter(
        dt,  # type: ignore[arg-type]  # duck-typed MCP client
        text_stats=lambda _path: stats,
        report=reported.append,
        **kwargs,
    )
    return importer, reported


class TestParsePathComponents:
    """Routing rules carried over from parsePathComponents in the AppleScript."""

    @pytest.mark.parametrize(
        ("relative_path", "database", "group_path", "is_inbox", "is_root_level"),
        [
            ("Liberty.University/file.pdf", "Liberty.University", "", False, True),
            ("Liberty.University/BUSI770/Week01/file.pdf", "Liberty.University", "BUSI770/Week01", False, False),
            ("Liberty.University/Inbox/file.pdf", "Liberty.University", "", True, False),
            ("Liberty.University/Inbox/Project/Sub/file.pdf", "Liberty.University", "Project/Sub", True, False),
            ("Liberty.University/inbox/Project/file.pdf", "Liberty.University", "Project", True, False),
        ],
    )
    def test_routing(self, relative_path: str, database: str, group_path: str, is_inbox: bool, is_root_level: bool) -> None:
        """Should split database, group path and inbox routing like the AppleScript."""
        c = parse_path_components(relative_path)
        assert (c.database, c.group_path, c.is_inbox, c.is_root_level) == (
            database, group_path, is_inbox, is_root_level,
        )

    def test_file_outside_database_folder(self) -> None:
        """Should reject a file sitting directly in the watch folder (error 1001)."""
        with pytest.raises(ImporterError) as exc:
            parse_path_components("file.pdf")
        assert exc.value.code == errors.NOT_IN_DATABASE_FOLDER


class TestNeedsOcr:
    """The OCR decision must keep the measured calibration exactly."""

    @pytest.mark.parametrize(
        ("pages", "per_page", "expected"),
        [
            (0, 0, True),  # unparseable: cannot prove it has text
            (1, 0, True),  # true scan
            (53, 41, True),  # near-scan
            (10, 91, True),  # ambiguous
            (12, 146, True),  # thin born-digital: still OCR'd, threshold is deliberately high
            (12, 999, True),
            (12, 1000, False),
            (2, 5022, False),  # full text layer
        ],
    )
    def test_calibration_bands(self, pages: int, per_page: int, expected: bool) -> None:
        """Should OCR unless the text layer is unmistakably complete."""
        assert needs_ocr(pages, per_page) is expected


class TestHashContract:
    """sourcehash must equal the SHA-256 other tools compute for the same file.

    RAP independently stamps this value into ~1,000 Obsidian notes; nothing
    else would notice if the two drifted apart.
    """

    def test_matches_shasum(self, pdf: Path) -> None:
        """Should equal `shasum -a 256`, which the AppleScript used."""
        shasum = subprocess.run(
            ["shasum", "-a", "256", str(pdf)], capture_output=True, text=True, check=True
        ).stdout.split()[0]
        assert sha256_file(pdf) == shasum

    @pytest.mark.skipif(not RAP_PROJECT.is_dir(), reason="research_analysis_platform not checked out")
    def test_matches_rap_calculate_file_hash(self, pdf: Path) -> None:
        """Should equal the hash RAP writes into Obsidian frontmatter."""
        rap = subprocess.run(
            [
                "uv", "run", "--quiet", "--project", str(RAP_PROJECT), "python", "-c",
                "import sys; from research_assistant_platform.functions.generic.file_utils "
                "import calculate_file_hash; print(calculate_file_hash(sys.argv[1]))",
                str(pdf),
            ],
            capture_output=True, text=True, timeout=120, check=True,
        ).stdout.strip().splitlines()[-1]
        assert sha256_file(pdf) == rap

    def test_missing_file_raises_1007(self, tmp_path: Path) -> None:
        """Should report a hashing failure with the AppleScript's error code."""
        with pytest.raises(ImporterError) as exc:
            sha256_file(tmp_path / "missing.pdf")
        assert exc.value.code == errors.HASH_FAILED


def hit(uuid: str, *, raw: bool, in_dest: bool = True, added: str = "2026-01-01") -> Hit:
    return Hit(uuid=uuid, raw=raw, in_dest=in_dest, added=added)


class TestDecideExisting:
    """The reconciliation table in decide_existing's docstring."""

    def test_processed_hit_already_filed(self) -> None:
        """Re-running a finished import changes nothing."""
        plan = decide_existing([hit("P", raw=False)], ocr_wanted=True)
        assert plan.keep == "P" and plan.ocr is None
        assert plan.trash == ()
        assert plan.status == "replicated"

    def test_crash_after_ocr_before_trash(self) -> None:
        """A raw leftover beside its OCR'd copy is trashed; the OCR'd copy is kept."""
        plan = decide_existing([hit("R", raw=True), hit("P", raw=False)], ocr_wanted=True)
        assert plan.keep == "P" and plan.ocr is None
        assert set(plan.trash) == {"R"}
        assert plan.status == "recovered"

    def test_crash_before_ocr(self) -> None:
        """A lone raw record is OCR'd, then trashed."""
        plan = decide_existing([hit("R", raw=True)], ocr_wanted=True)
        assert plan.ocr == "R" and plan.keep is None
        assert set(plan.trash) == {"R"}
        assert plan.status == "recovered"

    def test_several_raw_leftovers(self) -> None:
        """One raw record is OCR'd and every raw copy is trashed."""
        plan = decide_existing([hit("R1", raw=True), hit("R2", raw=True)], ocr_wanted=True)
        assert plan.ocr in {"R1", "R2"}
        assert set(plan.trash) == {"R1", "R2"}

    def test_born_digital_duplicate(self) -> None:
        """Without OCR, a raw record is a finished import: keep it, trash nothing."""
        plan = decide_existing([hit("R", raw=True, in_dest=False)], ocr_wanted=False)
        assert plan.keep == "R" and plan.ocr is None
        assert plan.trash == ()
        assert plan.status == "replicated"

    def test_never_trashes_processed_records(self) -> None:
        """Processed records -- OCR'd or annotated -- are never trash candidates."""
        hits = [hit("P1", raw=False), hit("P2", raw=False, in_dest=False), hit("R", raw=True)]
        for ocr_wanted in (True, False):
            plan = decide_existing(hits, ocr_wanted)
            assert not {"P1", "P2"} & set(plan.trash)
            assert plan.keep in {"P1", "P2", "R", None}


class TestApplySafety:
    """The applier must refuse unsafe plans before making any change."""

    MUTATING = {"trash_record", "ocr_record", "replicate_record", "set_record_custom_metadata"}

    @pytest.mark.parametrize(
        "plan",
        [
            Plan(status="recovered", keep="P", trash=("P",)),  # trash what it keeps
            Plan(status="recovered", keep="R", trash=("P",)),  # trash a processed record
            Plan(status="recovered", ocr="P", trash=()),  # OCR a processed record
            Plan(status="recovered", keep="R", ocr="R"),  # both keep and ocr
            Plan(status="recovered"),  # neither
            Plan(status="replicated", keep="NOT-A-HIT"),
        ],
    )
    def test_refuses_unsafe_plan(self, dt: FakeDevonthink, plan: Plan) -> None:
        """Should raise UNSAFE_PLAN and touch nothing."""
        importer, _ = make_importer(dt, SCANNED)
        hits = [hit("R", raw=True), hit("P", raw=False)]
        with pytest.raises(ImporterError) as exc:
            importer._apply(plan, hits, "hash", "DB", "DEST")
        assert exc.value.code == errors.UNSAFE_PLAN
        assert not self.MUTATING & {name for name, _ in dt.calls}

    def test_trash_rechecks_bytes(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should not trash a record whose file changed since it was classified raw."""
        importer, reported = make_importer(dt, SCANNED)
        source_hash = sha256_file(pdf)
        keep = dt.add_record(pdf, "DEST", stamp=source_hash, processed=True)
        stale = dt.add_record(pdf, "DEST", stamp=source_hash, processed=False)
        dt.records[stale]["path"].write_bytes(b"annotated since the search")

        importer._apply(
            Plan(status="recovered", keep=keep, trash=(stale,)),
            [hit(keep, raw=False), hit(stale, raw=True)],
            source_hash, "DB", "DEST",
        )
        assert not dt.records[stale]["trashed"]
        assert any("not trashing" in line for line in reported)


class TestImportNew:
    """Fresh imports: no record carries the file's hash yet."""

    def test_scanned_pdf_is_ocrd_and_original_trashed(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should leave exactly one live record: the OCR'd copy, stamped, in place."""
        importer, _ = make_importer(dt, SCANNED)
        result = importer.run(pdf, "Liberty.University/BUSI770/Week01/paper.pdf")

        assert result.status == "success"
        assert list(dt.live()) == [result.uuid]
        kept = dt.records[result.uuid]
        assert kept["meta"]["sourcehash"] == sha256_file(pdf)
        assert kept["parents"] == {dt.groups["/BUSI770/Week01"]}
        assert sha256_file(kept["path"]) != sha256_file(pdf)  # it is the OCR'd copy

    def test_stamp_precedes_ocr(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should stamp before OCR, so an interrupted run leaves a findable record."""
        importer, _ = make_importer(dt, SCANNED)
        importer.run(pdf, "Liberty.University/paper.pdf")
        order = [name for name, _ in dt.calls]
        assert order.index("set_record_custom_metadata") < order.index("ocr_record")

    def test_stamp_uses_merge(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should never use the default mode, which replaces all custom metadata."""
        importer, _ = make_importer(dt, SCANNED)
        importer.run(pdf, "Liberty.University/paper.pdf")
        assert all(args["mode"] == "merge" for args in dt.called("set_record_custom_metadata"))

    def test_born_digital_skips_ocr(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should import a full-text PDF as-is."""
        importer, _ = make_importer(dt, BORN_DIGITAL)
        result = importer.run(pdf, "Liberty.University/BUSI770/paper.pdf")

        assert result.status == "imported"
        assert not dt.called("ocr_record")
        assert not dt.called("trash_record")

    @pytest.mark.parametrize("behaviour", ["timeout", "error"])
    def test_ocr_failure_keeps_original(self, dt: FakeDevonthink, pdf: Path, behaviour: str) -> None:
        """Should keep the stamped original and warn, as the AppleScript did."""
        dt.ocr_behaviour = behaviour
        importer, reported = make_importer(dt, SCANNED)
        result = importer.run(pdf, "Liberty.University/paper.pdf")

        assert result.status == "imported"
        assert not dt.called("trash_record")
        assert dt.records[result.uuid]["meta"]["sourcehash"] == sha256_file(pdf)
        assert any(line.startswith("WARNING: OCR") for line in reported)

    def test_unsearchable_result_fails_1008(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should fail when the landed record has no words at all."""
        dt.ocr_behaviour = "error"
        dt.raw_word_count = 0
        importer, _ = make_importer(dt, SCANNED)
        with pytest.raises(ImporterError) as exc:
            importer.run(pdf, "Liberty.University/paper.pdf")
        assert exc.value.code == errors.NOT_SEARCHABLE

    def test_root_level_goes_to_incoming_group(self, dt: FakeDevonthink, pdf: Path) -> None:
        """<db>/file.pdf should land in the incoming group without creating groups."""
        importer, _ = make_importer(dt, BORN_DIGITAL)
        result = importer.run(pdf, "Liberty.University/paper.pdf")
        assert dt.records[result.uuid]["parents"] == {"INBOX"}
        assert not dt.called("create_group_path")

    def test_inbox_subgroups_use_real_inbox_name(self, dt: FakeDevonthink, pdf: Path) -> None:
        """<db>/Inbox/<groups>/ should build the path from the incoming group's name."""
        dt.group_props["INBOX"] = {"location": "/", "name": "Eingang"}
        importer, _ = make_importer(dt, BORN_DIGITAL)
        importer.run(pdf, "Liberty.University/Inbox/Week01/paper.pdf")
        assert dt.called("create_group_path")[0]["location"] == "/Eingang/Week01"

    def test_unknown_database_fails_1002(self, dt: FakeDevonthink, pdf: Path) -> None:
        importer, _ = make_importer(dt, BORN_DIGITAL)
        with pytest.raises(ImporterError) as exc:
            importer.run(pdf, "No.Such.Database/paper.pdf")
        assert exc.value.code == errors.DATABASE_NOT_FOUND

    def test_enrichment_runs_when_doi_detected(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should resolve a detected DOI and keep the stamp."""
        importer, _ = make_importer(dt, BORN_DIGITAL)
        original_import = dt._import_file

        def import_with_doi(**kwargs: Any) -> dict[str, Any]:
            rec = original_import(**kwargs)
            dt.records[rec["uuid"]]["doi"] = "10.1111/peps.12229"
            return rec

        dt._import_file = import_with_doi  # type: ignore[method-assign]
        result = importer.run(pdf, "Liberty.University/paper.pdf")

        meta = dt.records[result.uuid]["meta"]
        assert meta["journal"] == "Personnel Psychology"
        assert meta["sourcehash"] == sha256_file(pdf)
        assert dt.called("resolve_doi_metadata")[0]["rename"] is False


class TestRecovery:
    """Re-runs and interrupted runs, resolved through the hash search."""

    def test_rerun_after_success_changes_nothing(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should report replicated and create or trash nothing."""
        importer, _ = make_importer(dt, SCANNED)
        first = importer.run(pdf, "Liberty.University/paper.pdf")
        before = dict(dt.live())

        second = importer.run(pdf, "Liberty.University/paper.pdf")
        assert second.status == "replicated"
        assert second.uuid == first.uuid
        assert dt.live().keys() == before.keys()

    def test_existing_record_replicated_to_new_group(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should replicate an existing record into a different destination."""
        importer, _ = make_importer(dt, SCANNED)
        first = importer.run(pdf, "Liberty.University/BUSI770/paper.pdf")
        second = importer.run(pdf, "Liberty.University/BUSI771/paper.pdf")

        assert second.status == "replicated"
        assert dt.records[first.uuid]["parents"] == {
            dt.groups["/BUSI770"], dt.groups["/BUSI771"],
        }

    def test_crash_before_ocr_is_repaired(self, dt: FakeDevonthink, pdf: Path) -> None:
        """A stamped, never-OCR'd record should be OCR'd and replaced."""
        raw = dt.add_record(pdf, "INBOX", stamp=sha256_file(pdf), processed=False)
        importer, _ = make_importer(dt, SCANNED)
        result = importer.run(pdf, "Liberty.University/paper.pdf")

        assert result.status == "recovered"
        assert dt.records[raw]["trashed"]
        assert list(dt.live()) == [result.uuid]
        assert dt.records[result.uuid]["words"] > dt.raw_word_count

    def test_crash_after_ocr_is_tidied(self, dt: FakeDevonthink, pdf: Path) -> None:
        """A leftover original beside its OCR'd copy should be trashed."""
        stamp = sha256_file(pdf)
        processed = dt.add_record(pdf, "INBOX", stamp=stamp, processed=True)
        raw = dt.add_record(pdf, "INBOX", stamp=stamp, processed=False)
        importer, _ = make_importer(dt, SCANNED)
        result = importer.run(pdf, "Liberty.University/paper.pdf")

        assert result.status == "recovered"
        assert result.uuid == processed
        assert dt.records[raw]["trashed"]
        assert not dt.called("ocr_record")

    def test_annotated_record_is_never_trashed(self, dt: FakeDevonthink, pdf: Path) -> None:
        """A record whose bytes differ from the source must survive any re-run."""
        annotated = dt.add_record(pdf, "INBOX", stamp=sha256_file(pdf), processed=True)
        importer, _ = make_importer(dt, SCANNED)
        importer.run(pdf, "Liberty.University/paper.pdf")
        assert not dt.records[annotated]["trashed"]


class TestEnrichment:
    """Bibliographic enrichment is best-effort and never guesses."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("978-0-306-40615-7", True),
            ("0306406152", True),
            ("030640615X", True),
            ("0031-5826", False),  # an ISSN, which DOI resolution writes into is?n
            ("", False),
        ],
    )
    def test_looks_like_isbn(self, value: str, expected: bool) -> None:
        assert looks_like_isbn(value) is expected

    def test_issn_is_not_sent_to_book_lookup(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should skip enrichment rather than look up a book by ISSN."""
        uuid = dt.add_record(pdf, "INBOX", stamp="h", processed=True)
        dt.records[uuid]["meta"]["is?n"] = "0031-5826"
        reported: list[str] = []
        assert enrich_record(dt, uuid, "DB", report=reported.append) is None  # type: ignore[arg-type]
        assert not dt.called("resolve_book_metadata")

    def test_failure_is_not_fatal(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should report and carry on when the resolver fails."""
        uuid = dt.add_record(pdf, "INBOX", stamp="h", processed=True)
        dt.records[uuid]["doi"] = "10.1/x"

        def unreachable(**_: Any) -> None:
            raise MCPToolError("resolve_doi_metadata: CrossRef unreachable")

        dt._resolve_doi_metadata = unreachable  # type: ignore[method-assign]
        reported: list[str] = []
        assert enrich_record(dt, uuid, "DB", report=reported.append) is None  # type: ignore[arg-type]
        assert any("enrichment failed" in line for line in reported)
