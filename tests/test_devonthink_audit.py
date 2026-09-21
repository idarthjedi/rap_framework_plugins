"""Tests for the read-only import audit."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rap_importer_plugin.devonthink.audit import (
    READ_ONLY_TOOLS,
    Auditor,
    AuditRow,
    ReadOnlyClient,
    format_row,
    select_files,
)
from rap_importer_plugin.devonthink.importer import sha256_file
from rap_importer_plugin.devonthink.pdf_info import PdfInfo

from .devonthink_fake import MUTATING, FakeDevonthink


@pytest.fixture
def dt(tmp_path: Path) -> FakeDevonthink:
    return FakeDevonthink(tmp_path)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "_Archived"
    (folder / "Liberty.University" / "Harvard Business Review").mkdir(parents=True)
    return folder


def archived(root: Path, name: str, content: bytes) -> Path:
    path = root / "Liberty.University" / "Harvard Business Review" / name
    path.write_bytes(content)
    return path


def auditor(dt: FakeDevonthink, pages: int = 1) -> Auditor:
    return Auditor(dt, inspect_pdf=lambda _path: PdfInfo(pages, False))


class TestReadOnlyClient:
    """The audit must be unable to change DEVONthink."""

    @pytest.mark.parametrize("tool", sorted(MUTATING))
    def test_refuses_mutating_tools(self, dt: FakeDevonthink, tool: str) -> None:
        with pytest.raises(PermissionError, match="read-only"):
            ReadOnlyClient(dt).call_tool(tool, {})
        assert not dt.calls  # refused before reaching the server

    def test_allowlist_is_read_only(self) -> None:
        assert not READ_ONLY_TOOLS & MUTATING
        assert all(t.startswith(("get_", "search_")) for t in READ_ONLY_TOOLS)

    def test_passes_read_only_tools_through(self, dt: FakeDevonthink) -> None:
        assert ReadOnlyClient(dt).call_tool("get_databases") == [dt.database]


class TestAuditor:
    """Classification of each file."""

    def test_found(self, dt: FakeDevonthink, root: Path) -> None:
        path = archived(root, "H099N1-PDF-ENG.pdf", b"%PDF hbr article")
        record = dt.add_record(path, "INBOX", stamp=sha256_file(path), name="The Science of Developing Creative Talent")

        row = auditor(dt).audit(path, root)

        assert row.status == "found"
        assert row.records[0]["uuid"] == record
        assert row.records[0]["name"] == "The Science of Developing Creative Talent"

    def test_duplicate(self, dt: FakeDevonthink, root: Path) -> None:
        path = archived(root, "a.pdf", b"%PDF a")
        dt.add_record(path, "INBOX", stamp=sha256_file(path))
        dt.add_record(path, "INBOX", stamp=sha256_file(path), processed=True)
        assert auditor(dt).audit(path, root).status == "duplicate"

    def test_ambiguous(self, dt: FakeDevonthink, root: Path) -> None:
        path = archived(root, "a.pdf", b"%PDF a")
        dt.add_record(path, "INBOX", stamp=sha256_file(path), pages=9)
        row = auditor(dt, pages=6).audit(path, root)
        assert row.status == "ambiguous"
        assert row.detail == "its page count differs (9 vs 6)"

    def test_trashed(self, dt: FakeDevonthink, root: Path) -> None:
        """The hash search cannot see the trash, so the trash is listed instead."""
        path = archived(root, "a.pdf", b"%PDF a")
        dt.add_record(path, "TRASH", stamp=sha256_file(path), trashed=True)
        row = auditor(dt).audit(path, root)
        assert row.status == "trashed"
        assert row.records[0]["location"] == "/Trash/"

    def test_missing(self, dt: FakeDevonthink, root: Path) -> None:
        path = archived(root, "a.pdf", b"%PDF never imported")
        assert auditor(dt).audit(path, root).status == "missing"

    def test_skipped_outside_database_folder(self, dt: FakeDevonthink, root: Path) -> None:
        path = root / "stray.pdf"
        path.write_bytes(b"%PDF")
        assert auditor(dt).audit(path, root).status == "skipped"

    def test_skipped_database_not_open(self, dt: FakeDevonthink, root: Path) -> None:
        (root / "Other.Database").mkdir()
        path = root / "Other.Database" / "a.pdf"
        path.write_bytes(b"%PDF")
        row = auditor(dt).audit(path, root)
        assert row.status == "skipped"
        assert "not open" in row.detail

    def test_makes_no_changes(self, dt: FakeDevonthink, root: Path) -> None:
        paths = [archived(root, f"{i}.pdf", f"%PDF {i}".encode()) for i in range(3)]
        dt.add_record(paths[0], "INBOX", stamp=sha256_file(paths[0]))
        dt.add_record(paths[1], "TRASH", stamp=sha256_file(paths[1]), trashed=True)
        a = auditor(dt)
        for path in paths:
            a.audit(path, root)
        assert not dt.mutations()


class TestSelectFiles:
    """Choosing which archived files to audit."""

    def test_all_pdfs(self, root: Path) -> None:
        archived(root, "a.pdf", b"%PDF")
        archived(root, "notes.txt", b"not a pdf")
        assert [p.name for p in select_files(root, None)] == ["a.pdf"]

    def test_since_uses_inode_change_time(self, root: Path) -> None:
        archived(root, "a.pdf", b"%PDF")
        assert select_files(root, datetime.now() - timedelta(hours=1))
        assert not select_files(root, datetime.now() + timedelta(hours=1))


class TestFormatRow:
    def test_found_row(self) -> None:
        row = AuditRow("HBR/H099N1.pdf", "found", [{
            "name": "The Science of Developing Creative Talent", "uuid": "A3C8BD54-CF4C",
            "location": "/Harvard Business Review/", "kind": "PDF+Text", "wordCount": 1814,
        }])
        assert format_row(row) == (
            'FOUND      HBR/H099N1.pdf  ->  "The Science of Developing Creative Talent" '
            "/Harvard Business Review/ (A3C8BD54, PDF+Text, 1814 words)"
        )

    def test_missing_row(self) -> None:
        assert format_row(AuditRow("a.pdf", "missing")) == "MISSING    a.pdf"
