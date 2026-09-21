"""Tests for the MCP-based DEVONthink importer."""

from __future__ import annotations

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
    ImportResult,
    choose_existing,
    identity_problem,
    needs_ocr,
    parse_path_components,
    result_lines,
    sha256_file,
)
from rap_importer_plugin.devonthink.mcp_client import MCPToolError
from rap_importer_plugin.devonthink.pdf_info import PdfInfo

from .devonthink_fake import FakeDevonthink

RAP_PROJECT = Path.home() / "development/anthropics/projects/research_analysis_platform"

SCANNED = (1, 0, 0)  # pdf_text_stats output for an image-only page
BORN_DIGITAL = (1, 5022, 5022)
ENCRYPTED = (1, 0, 0)  # encrypted content streams read as no text


@pytest.fixture
def dt(tmp_path: Path) -> FakeDevonthink:
    return FakeDevonthink(tmp_path)


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF-1.4 source bytes of the incoming paper")
    return source


def make_importer(
    dt: FakeDevonthink,
    stats: tuple[int, int, int],
    *,
    pages: int = 1,
    encrypted: bool = False,
    **kwargs: Any,
) -> tuple[DevonthinkImporter, list[str]]:
    reported: list[str] = []
    importer = DevonthinkImporter(
        dt,  # type: ignore[arg-type]  # duck-typed MCP client
        text_stats=lambda _path: stats,
        inspect_pdf=lambda _path: PdfInfo(pages=pages, encrypted=encrypted),
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


def props(**overrides: Any) -> dict[str, Any]:
    base = {"type": "pdf", "kind": "PDF+Text", "pageCount": 6, "customMetadata": {"sourcehash": "h" * 64}}
    return {**base, **overrides}


class TestIdentityProblem:
    """A hash match must also prove it is the same document."""

    def test_verified(self) -> None:
        assert identity_problem(props(), "h" * 64, 6) == ""

    def test_stamp_must_match_exactly(self) -> None:
        """Should not trust the search operator: a longer stamp is not a match."""
        p = props(customMetadata={"sourcehash": "h" * 64 + "0"})
        assert "sourcehash differs" in identity_problem(p, "h" * 64, 6)

    def test_missing_stamp(self) -> None:
        assert "sourcehash differs" in identity_problem(props(customMetadata={}), "h" * 64, 6)

    def test_must_be_a_pdf(self) -> None:
        """A converted or derived record can inherit the stamp without being the document."""
        assert "not a PDF" in identity_problem(props(type="markdown", kind="Markdown"), "h" * 64, 6)

    def test_page_count_must_agree(self) -> None:
        assert identity_problem(props(pageCount=9), "h" * 64, 6) == "its page count differs (9 vs 6)"

    def test_unknown_incoming_page_count(self) -> None:
        assert "could not be read" in identity_problem(props(), "h" * 64, 0)

    def test_unknown_record_page_count(self) -> None:
        assert "unknown" in identity_problem(props(pageCount=0), "h" * 64, 6)


def hit(uuid: str, *, in_dest: bool = True, added: str = "2026-01-01", problem: str = "") -> Hit:
    return Hit(uuid=uuid, name=uuid, in_dest=in_dest, added=added, problem=problem)


class TestChooseExisting:
    """Which verified record to file the document as."""

    def test_none_verified(self) -> None:
        assert choose_existing([hit("A", problem="its page count differs (9 vs 6)")]) is None

    def test_prefers_record_already_in_destination(self) -> None:
        chosen = choose_existing([hit("OLD", in_dest=False, added="2020"), hit("HERE", added="2026")])
        assert chosen is not None and chosen.uuid == "HERE"

    def test_then_prefers_oldest(self) -> None:
        chosen = choose_existing([hit("NEW", added="2026-09-21"), hit("OLD", added="2024-02-12")])
        assert chosen is not None and chosen.uuid == "OLD"

    def test_ignores_unverified_even_in_destination(self) -> None:
        chosen = choose_existing([hit("BAD", problem="it is a Markdown, not a PDF"), hit("GOOD", in_dest=False)])
        assert chosen is not None and chosen.uuid == "GOOD"


class TestFileExisting:
    """A file whose hash is already in DEVONthink: replicate a verified record, never change it."""

    def test_curated_record_is_never_replaced(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Regression for the 2026-09-21 incident.

        An AppleScript-era record: never OCR'd (so byte-identical to the source),
        renamed by DEVONthink, carrying metadata. OCR is wanted for the incoming
        file. The old logic planned to OCR this record and trash it.
        """
        group = dt.group("/Harvard Business Review")
        curated = dt.add_record(
            pdf, group, stamp=sha256_file(pdf), processed=False,
            name="The Science of Developing Creative Talent",
            metadata={"author": "Deshmane", "abstract": "An HBR article."},
        )
        before = dict(dt.records[curated], meta=dict(dt.records[curated]["meta"]))
        importer, _ = make_importer(dt, SCANNED)

        result = importer.run(pdf, "Liberty.University/Harvard Business Review/H099N1-PDF-ENG.pdf")

        assert result.status == "replicated"
        assert result.uuid == curated
        assert result.name == "The Science of Developing Creative Talent"
        assert dt.mutations() == {"create_group_path"}  # nothing else changed
        assert dt.records[curated]["meta"] == before["meta"]
        assert dt.records[curated]["name"] == before["name"]
        assert not dt.records[curated]["trashed"]

    def test_rerun_after_success_changes_nothing(self, dt: FakeDevonthink, pdf: Path) -> None:
        importer, _ = make_importer(dt, SCANNED)
        first = importer.run(pdf, "Liberty.University/paper.pdf")
        before = set(dt.live())
        dt.calls.clear()

        second = make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/paper.pdf")

        assert second.status == "replicated"
        assert second.uuid == first.uuid
        assert set(dt.live()) == before
        assert not dt.mutations()

    def test_replicated_into_new_group(self, dt: FakeDevonthink, pdf: Path) -> None:
        first = make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/BUSI770/paper.pdf")
        second = make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/BUSI771/paper.pdf")

        assert second.status == "replicated"
        assert second.location == "/BUSI771/"
        assert dt.records[first.uuid]["parents"] == {dt.groups["/BUSI770"], dt.groups["/BUSI771"]}

    @pytest.mark.parametrize(
        ("arrange", "message"),
        [
            (lambda dt, pdf, h: dt.add_record(pdf, "INBOX", stamp=h + "0"), "sourcehash differs"),
            (lambda dt, pdf, h: dt.add_record(pdf, "INBOX", stamp=h, record_type="markdown"), "not a PDF"),
            (lambda dt, pdf, h: dt.add_record(pdf, "INBOX", stamp=h, pages=9, name="Merged Reader"),
             "matches 'Merged Reader' by hash but its page count differs (9 vs 1)"),
        ],
        ids=["stamp-substring", "not-a-pdf", "page-count"],
    )
    def test_unverifiable_match_fails_and_changes_nothing(
        self, dt: FakeDevonthink, pdf: Path, arrange: Any, message: str
    ) -> None:
        """Should FAIL 1012 -- visible, and the file stays in the import folder -- not file it."""
        arrange(dt, pdf, sha256_file(pdf))
        importer, _ = make_importer(dt, SCANNED)
        with pytest.raises(ImporterError) as exc:
            importer.run(pdf, "Liberty.University/paper.pdf")
        assert exc.value.code == errors.AMBIGUOUS_MATCH
        assert message in str(exc.value)
        assert not dt.mutations()

    def test_unknown_incoming_page_count_fails(self, dt: FakeDevonthink, pdf: Path) -> None:
        dt.add_record(pdf, "INBOX", stamp=sha256_file(pdf))
        importer, _ = make_importer(dt, (0, 0, 0), pages=0)
        with pytest.raises(ImporterError) as exc:
            importer.run(pdf, "Liberty.University/paper.pdf")
        assert exc.value.code == errors.AMBIGUOUS_MATCH

    def test_verified_record_chosen_over_unverified(self, dt: FakeDevonthink, pdf: Path) -> None:
        stamp = sha256_file(pdf)
        dt.add_record(pdf, "INBOX", stamp=stamp, record_type="markdown", name="summary")
        real = dt.add_record(pdf, "INBOX", stamp=stamp, name="the paper")
        result = make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/paper.pdf")
        assert result.uuid == real

    def test_leftover_pair_is_left_alone(self, dt: FakeDevonthink, pdf: Path) -> None:
        """An un-OCR'd record beside an OCR'd one is reported, never tidied."""
        stamp = sha256_file(pdf)
        older = dt.add_record(pdf, "INBOX", stamp=stamp, processed=False, added="2026-09-19")
        dt.add_record(pdf, "INBOX", stamp=stamp, processed=True, added="2026-09-20")
        result = make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/paper.pdf")
        assert result.uuid == older
        assert not dt.mutations()

    def test_replicate_that_does_not_land_fails(self, dt: FakeDevonthink, pdf: Path) -> None:
        dt.add_record(pdf, "INBOX", stamp=sha256_file(pdf))
        dt.replicate_behaviour = "noop"
        with pytest.raises(ImporterError) as exc:
            make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/BUSI770/paper.pdf")
        assert exc.value.code == errors.REPLICATE_FAILED


class TestTrashGuard:
    """Only records created by the same run may ever be trashed."""

    def test_refuses_record_not_created_this_run(self, dt: FakeDevonthink, pdf: Path) -> None:
        existing = dt.add_record(pdf, "INBOX", stamp="h")
        importer, _ = make_importer(dt, SCANNED)
        with pytest.raises(ImporterError) as exc:
            importer._trash_created(existing, "DB")
        assert exc.value.code == errors.UNSAFE_TRASH
        assert not dt.called("trash_record")
        assert not dt.records[existing]["trashed"]


class TestImportNew:
    """Fresh imports: no record carries the file's hash yet."""

    def test_scanned_pdf_is_ocrd_and_original_trashed(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should leave exactly one live record: the OCR'd copy, stamped, in place."""
        result = make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/BUSI770/Week01/paper.pdf")

        assert result.status == "success"
        assert list(dt.live()) == [result.uuid]
        kept = dt.records[result.uuid]
        assert kept["meta"]["sourcehash"] == sha256_file(pdf)
        assert kept["parents"] == {dt.groups["/BUSI770/Week01"]}
        assert result.location == "/BUSI770/Week01/"
        assert len(dt.called("trash_record")) == 1

    def test_stamp_precedes_ocr(self, dt: FakeDevonthink, pdf: Path) -> None:
        make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/paper.pdf")
        order = [name for name, _ in dt.calls]
        assert order.index("set_record_custom_metadata") < order.index("ocr_record")

    def test_stamp_uses_merge(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Should never use the default mode, which replaces all custom metadata."""
        make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/paper.pdf")
        assert all(args["mode"] == "merge" for args in dt.called("set_record_custom_metadata"))

    def test_born_digital_skips_ocr(self, dt: FakeDevonthink, pdf: Path) -> None:
        result = make_importer(dt, BORN_DIGITAL)[0].run(pdf, "Liberty.University/BUSI770/paper.pdf")
        assert result.status == "imported"
        assert not dt.called("ocr_record")
        assert not dt.called("trash_record")

    def test_encrypted_pdf_skips_ocr(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Password-protected PDFs read as 0 chars/page, but OCR always fails on them."""
        importer, reported = make_importer(dt, ENCRYPTED, encrypted=True)
        result = importer.run(pdf, "Liberty.University/Harvard Business Review/H04XBL-PDF-ENG.pdf")
        assert result.status == "imported"
        assert not dt.called("ocr_record")
        assert "Password-protected PDF; OCR skipped" in reported

    @pytest.mark.parametrize(("behaviour", "note"), [("timeout", "OCR did not finish"), ("error", "OCR failed")])
    def test_ocr_failure_keeps_original(self, dt: FakeDevonthink, pdf: Path, behaviour: str, note: str) -> None:
        """Should keep the stamped original, as the AppleScript did."""
        dt.ocr_behaviour = behaviour
        importer, reported = make_importer(dt, SCANNED)
        result = importer.run(pdf, "Liberty.University/paper.pdf")

        assert result.status == "imported"
        assert not dt.called("trash_record")
        assert dt.records[result.uuid]["meta"]["sourcehash"] == sha256_file(pdf)
        assert any(line.startswith(note) for line in reported)

    @pytest.mark.parametrize(("behaviour", "note"), [
        ("same", "OCR returned the original record"),
        ("unstamped", "could not be verified"),
    ])
    def test_unverified_ocr_copy_never_costs_the_original(
        self, dt: FakeDevonthink, pdf: Path, behaviour: str, note: str
    ) -> None:
        """B4: trash the original only once a distinct, stamped copy exists."""
        dt.ocr_behaviour = behaviour
        importer, reported = make_importer(dt, SCANNED)
        result = importer.run(pdf, "Liberty.University/paper.pdf")

        assert result.status == "imported"
        assert not dt.called("trash_record")
        assert not dt.records[result.uuid]["trashed"]
        assert any(note in line for line in reported)

    def test_unsearchable_result_fails_1008(self, dt: FakeDevonthink, pdf: Path) -> None:
        dt.ocr_behaviour = "error"
        dt.raw_word_count = 0
        with pytest.raises(ImporterError) as exc:
            make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/paper.pdf")
        assert exc.value.code == errors.NOT_SEARCHABLE

    def test_root_level_goes_to_incoming_group(self, dt: FakeDevonthink, pdf: Path) -> None:
        result = make_importer(dt, BORN_DIGITAL)[0].run(pdf, "Liberty.University/paper.pdf")
        assert dt.records[result.uuid]["parents"] == {"INBOX"}
        assert result.location == "/Inbox/"
        assert not dt.called("create_group_path")

    def test_inbox_subgroups_use_real_inbox_name(self, dt: FakeDevonthink, pdf: Path) -> None:
        dt.group_props["INBOX"] = {"uuid": "INBOX", "location": "/", "name": "Eingang", "type": "group"}
        result = make_importer(dt, BORN_DIGITAL)[0].run(pdf, "Liberty.University/Inbox/Week01/paper.pdf")
        assert dt.called("create_group_path")[0]["location"] == "/Eingang/Week01"
        assert result.location == "/Eingang/Week01/"

    def test_unknown_database_fails_1002(self, dt: FakeDevonthink, pdf: Path) -> None:
        with pytest.raises(ImporterError) as exc:
            make_importer(dt, BORN_DIGITAL)[0].run(pdf, "No.Such.Database/paper.pdf")
        assert exc.value.code == errors.DATABASE_NOT_FOUND

    def test_enrichment_runs_when_doi_detected(self, dt: FakeDevonthink, pdf: Path) -> None:
        original_import = dt._import_file

        def import_with_doi(**kwargs: Any) -> dict[str, Any]:
            rec = original_import(**kwargs)
            dt.records[rec["uuid"]]["doi"] = "10.1111/peps.12229"
            return rec

        dt._import_file = import_with_doi  # type: ignore[method-assign]
        result = make_importer(dt, BORN_DIGITAL)[0].run(pdf, "Liberty.University/paper.pdf")

        meta = dt.records[result.uuid]["meta"]
        assert meta["journal"] == "Personnel Psychology"
        assert meta["sourcehash"] == sha256_file(pdf)
        assert dt.called("resolve_doi_metadata")[0]["rename"] is False

    def test_never_touches_database_files(self, dt: FakeDevonthink, pdf: Path) -> None:
        """Every path through the importer works through MCP properties alone."""
        make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/paper.pdf")
        make_importer(dt, SCANNED)[0].run(pdf, "Liberty.University/BUSI770/paper.pdf")
        assert not dt.called("get_imported_record_path")


class TestMain:
    """What the pipeline receives: exit status, stdout, and stderr."""

    @pytest.fixture
    def run_main(self, dt: FakeDevonthink, monkeypatch: pytest.MonkeyPatch):
        from rap_importer_plugin.devonthink import importer as importer_module

        class FakeClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __enter__(self) -> FakeDevonthink:
                return dt

            def __exit__(self, *exc: object) -> None:
                pass

        monkeypatch.setattr(importer_module, "MCPClient", FakeClient)

        def run(pdf: Path, relative: str) -> int:
            return importer_module.main([str(pdf), relative], text_stats=lambda _p: BORN_DIGITAL)

        return run

    def test_failure_reason_comes_before_timing(
        self, dt: FakeDevonthink, pdf: Path, run_main: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The notification banner shows stderr's first line, so it must be the reason."""
        dt.add_record(pdf, "INBOX", stamp=sha256_file(pdf), pages=9, name="Decoy")

        assert run_main(pdf, "Liberty.University/paper.pdf") == 1
        stderr = capsys.readouterr().err.splitlines()
        assert "matches 'Decoy' by hash" in stderr[0] and stderr[0].endswith("(1012)")
        assert stderr[1:] and all(line.startswith("TIMING:") for line in stderr[1:])

    def test_success_output(
        self, dt: FakeDevonthink, pdf: Path, run_main: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run_main(pdf, "Liberty.University/BUSI770/paper.pdf") == 0
        out = capsys.readouterr()
        lines = out.out.splitlines()
        assert lines[-4] == "imported"
        assert lines[-3].startswith("record=")
        assert lines[-2].endswith("location=/BUSI770/")
        assert lines[-1].startswith("sourcehash=")
        assert all(line.startswith("TIMING:") for line in out.err.splitlines())


class TestResultLines:
    """What the pipeline logs, so the log answers 'where did it go'."""

    def test_lines(self) -> None:
        result = ImportResult("replicated", "A3C8BD54", "ad0221cd", "The Science of Developing Creative Talent",
                              "/Harvard Business Review/")
        assert result_lines(result) == [
            "replicated",
            "record=A3C8BD54",
            'name="The Science of Developing Creative Talent" location=/Harvard Business Review/',
            "sourcehash=ad0221cd",
        ]


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
        uuid = dt.add_record(pdf, "INBOX", stamp="h", metadata={"is?n": "0031-5826"})
        assert enrich_record(dt, uuid, "DB", report=[].append) is None  # type: ignore[arg-type]
        assert not dt.called("resolve_book_metadata")

    def test_failure_is_not_fatal(self, dt: FakeDevonthink, pdf: Path) -> None:
        uuid = dt.add_record(pdf, "INBOX", stamp="h")
        dt.records[uuid]["doi"] = "10.1/x"

        def unreachable(**_: Any) -> None:
            raise MCPToolError("resolve_doi_metadata: CrossRef unreachable")

        dt._resolve_doi_metadata = unreachable  # type: ignore[method-assign]
        reported: list[str] = []
        assert enrich_record(dt, uuid, "DB", report=reported.append) is None  # type: ignore[arg-type]
        assert any("enrichment failed" in line for line in reported)
