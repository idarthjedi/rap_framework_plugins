"""Page count and encryption status of an incoming PDF, via macOS CoreGraphics.

Covers two things scripts/pdf_text_stats.py cannot do reliably:

- Encrypted PDFs (HBR articles carry an owner password, for example) keep their
  content streams encrypted on disk, so pdf_text_stats finds no text and reports
  0 chars/page -- which reads as "needs OCR", an OCR that always fails.
  CoreGraphics reports encryption directly.
- When a hash match is found, the page count is part of the identity check, so
  it has to be right for encrypted files too.

Only ever used on the incoming file, never on anything inside a DEVONthink
database. Uses ctypes, so there is no new dependency; the importer is
macOS-only anyway.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path


@dataclass(frozen=True)
class PdfInfo:
    pages: int  # 0 when the file could not be opened as a PDF
    encrypted: bool


@cache
def _frameworks() -> tuple[ctypes.CDLL, ctypes.CDLL] | None:
    cg_path = ctypes.util.find_library("CoreGraphics")
    cf_path = ctypes.util.find_library("CoreFoundation")
    if not cg_path or not cf_path:
        return None
    cg = ctypes.cdll.LoadLibrary(cg_path)
    cf = ctypes.cdll.LoadLibrary(cf_path)

    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool,
    ]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cg.CGPDFDocumentCreateWithURL.restype = ctypes.c_void_p
    cg.CGPDFDocumentCreateWithURL.argtypes = [ctypes.c_void_p]
    cg.CGPDFDocumentGetNumberOfPages.restype = ctypes.c_size_t
    cg.CGPDFDocumentGetNumberOfPages.argtypes = [ctypes.c_void_p]
    cg.CGPDFDocumentIsEncrypted.restype = ctypes.c_bool
    cg.CGPDFDocumentIsEncrypted.argtypes = [ctypes.c_void_p]
    cg.CGPDFDocumentRelease.argtypes = [ctypes.c_void_p]
    return cg, cf


def pdf_info(path: str | Path) -> PdfInfo:
    """Return the PDF's page count and whether it is encrypted.

    Returns PdfInfo(0, False) when the file cannot be opened as a PDF.
    """
    frameworks = _frameworks()
    if frameworks is None:
        return PdfInfo(0, False)
    cg, cf = frameworks

    raw = os.fsencode(path)
    url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
    if not url:
        return PdfInfo(0, False)
    try:
        doc = cg.CGPDFDocumentCreateWithURL(url)
    finally:
        cf.CFRelease(url)
    if not doc:
        return PdfInfo(0, False)
    try:
        return PdfInfo(
            pages=int(cg.CGPDFDocumentGetNumberOfPages(doc)),
            encrypted=bool(cg.CGPDFDocumentIsEncrypted(doc)),
        )
    finally:
        cg.CGPDFDocumentRelease(doc)
