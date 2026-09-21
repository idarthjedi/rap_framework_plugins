"""Tests for scripts/pdf_text_stats.py.

The helper feeds the OCR decision for both importers: the AppleScript runs it
under the system python3, the MCP importer imports it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from rap_importer_plugin.devonthink.importer import needs_ocr

_SCRIPT = Path(__file__).parent.parent / "scripts" / "pdf_text_stats.py"
_spec = importlib.util.spec_from_file_location("pdf_text_stats", _SCRIPT)
assert _spec and _spec.loader
pdf_text_stats = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pdf_text_stats)


def make_pdf(path: Path, pages: int, chars_per_page: int) -> Path:
    """Write a minimal uncompressed PDF with one Tj operator per page."""
    page_ids = [3 + 2 * i for i in range(pages)]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    parts = [
        b"%PDF-1.4\n",
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {pages} >> endobj\n".encode(),
    ]
    for pid in page_ids:
        content = b"BT (" + b"a" * chars_per_page + b") Tj ET" if chars_per_page else b""
        parts.append(
            f"{pid} 0 obj << /Type /Page /Parent 2 0 R /Contents {pid + 1} 0 R >> endobj\n".encode()
        )
        parts.append(
            f"{pid + 1} 0 obj << /Length {len(content)} >> stream\n".encode()
            + content
            + b"\nendstream endobj\n"
        )
    parts.append(b"%%EOF\n")
    path.write_bytes(b"".join(parts))
    return path


class TestTextStats:
    """Tests for text_stats and the OCR decision it feeds."""

    @pytest.mark.parametrize(
        ("pages", "chars", "ocr_expected"),
        [
            (1, 0, True),  # true scan
            (3, 40, True),  # near-scan
            (2, 90, True),  # ambiguous
            (4, 5000, False),  # born-digital
        ],
    )
    def test_calibration_bands(self, tmp_path: Path, pages: int, chars: int, ocr_expected: bool) -> None:
        """Should land each synthetic document in the right OCR band."""
        pdf = make_pdf(tmp_path / "doc.pdf", pages, chars)
        counted_pages, _, per_page = pdf_text_stats.text_stats(str(pdf))

        assert counted_pages == pages
        assert abs(per_page - chars) <= 1  # the operator estimate is approximate
        assert needs_ocr(counted_pages, per_page) is ocr_expected

    def test_unreadable_file_reports_zeros(self, tmp_path: Path) -> None:
        """Should return 0 0 0, which needs_ocr reads as 'OCR it'."""
        assert pdf_text_stats.text_stats(str(tmp_path / "missing.pdf")) == (0, 0, 0)
        assert needs_ocr(0, 0) is True
