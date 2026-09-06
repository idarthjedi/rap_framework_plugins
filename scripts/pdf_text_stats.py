#!/usr/bin/env python3
"""Report how much extractable text a PDF already carries.

Used by devonthink_importer.applescript to decide whether a document needs OCR.
Born-digital papers arrive with a full text layer and must NOT be sent to
DEVONthink's OCR engine: besides being wasted work, some of them wedge it
outright (it sits at 0% CPU and never replies, in any destination).

Deliberately dependency-free -- it runs under whatever python3 is on PATH, with
no venv and no install step, because it is called from AppleScript via
`do shell script`.

Output: three space-separated integers on one line

    <pages> <text_chars> <chars_per_page>

text_chars approximates the characters held in PDF text-showing operators
(Tj/TJ). It is an estimate, not a faithful extraction -- font encodings are not
resolved -- but it separates "has a real text layer" from "is a bare scan" by
orders of magnitude, which is all the OCR decision needs.

Exit status is always 0 with a parseable line, even for damaged files (0 0 0),
so the caller never has to distinguish a crash from a legitimate zero.
"""

from __future__ import annotations

import re
import subprocess
import sys
import zlib

# Text-showing operators: (string) Tj  and  [(a) -100 (b)] TJ
_TEXT_OP = re.compile(rb"\((?:[^()\\]|\\.)*\)\s*(?:TJ|Tj)", re.DOTALL)
_STREAM = re.compile(rb"stream\r?\n")


def _count_pages(blob: bytes) -> int:
    """Count page objects, discounting the /Pages tree nodes that also match."""
    pages = blob.count(b"/Type /Page") + blob.count(b"/Type/Page")
    pages -= blob.count(b"/Type /Pages") + blob.count(b"/Type/Pages")
    return max(pages, 0)


def _page_count_via_spotlight(path: str) -> int:
    """Ask macOS for the page count. Authoritative when Spotlight has the file."""
    try:
        out = subprocess.run(
            ["mdls", "-name", "kMDItemNumberOfPages", "-raw", path],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return int(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def text_stats(path: str) -> tuple[int, int, int]:
    """Return (pages, text_chars, chars_per_page) for a PDF."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return (0, 0, 0)

    # PDF 1.5+ hides page objects inside compressed object streams, so counting
    # the raw bytes alone reports 0 for most modern files. Walk the decompressed
    # streams as well, and let Spotlight override when it knows the answer.
    pages = _count_pages(data)
    chars = 0
    for match in _STREAM.finditer(data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end == -1:
            continue
        chunk = data[start:end]
        try:
            chunk = zlib.decompress(chunk)
        except zlib.error:
            # Uncompressed, or a filter we do not handle -- scan it as-is.
            pass
        else:
            pages += _count_pages(chunk)
        for op in _TEXT_OP.finditer(chunk):
            # Count only what is inside the parentheses, not the operator.
            chars += max(op.end() - op.start() - 4, 0)

    spotlight_pages = _page_count_via_spotlight(path)
    if spotlight_pages:
        pages = spotlight_pages

    per_page = chars // pages if pages else 0
    return (pages, chars, per_page)


def main() -> int:
    if len(sys.argv) < 2:
        print("0 0 0")
        return 0
    pages, chars, per_page = text_stats(sys.argv[1])
    print(f"{pages} {chars} {per_page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
