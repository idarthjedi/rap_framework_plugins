"""Tests for the CoreGraphics page count and encryption check."""

from __future__ import annotations

from pathlib import Path

from rap_importer_plugin.devonthink.pdf_info import PdfInfo, pdf_info

from .test_pdf_text_stats import pdf_text_stats

ENCRYPTED = Path(__file__).parent / "fixtures" / "encrypted_owner_password.pdf"


def valid_pdf(path: Path, pages: int) -> Path:
    """A minimal but well-formed PDF, cross-reference table included.

    CoreGraphics is a real PDF parser and rejects the looser files that
    pdf_text_stats' byte scanner is happy with.
    """
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
        *[b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>"] * pages,
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


class TestPdfInfo:
    """Tests for pdf_info."""

    def test_page_count(self, tmp_path: Path) -> None:
        assert pdf_info(valid_pdf(tmp_path / "doc.pdf", pages=4)) == PdfInfo(4, False)

    def test_owner_password_pdf(self) -> None:
        """Should report encryption for a PDF that opens without a password, like HBR's."""
        assert pdf_info(ENCRYPTED) == PdfInfo(3, True)

    def test_encrypted_pdf_fools_the_text_stats(self) -> None:
        """Why the check exists: encrypted streams read as no text, i.e. 'needs OCR'."""
        _, chars, _ = pdf_text_stats.text_stats(str(ENCRYPTED))
        assert chars == 0

    def test_unreadable_file(self, tmp_path: Path) -> None:
        not_pdf = tmp_path / "notes.pdf"
        not_pdf.write_text("not a pdf")
        assert pdf_info(not_pdf) == PdfInfo(0, False)
        assert pdf_info(tmp_path / "missing.pdf") == PdfInfo(0, False)
