"""Bibliographic enrichment of imported records.

Stands in for DEVONthink's stock "Download Bibliographic Metadata" smart rule,
which the AppleScript meant to trigger but never did: its trigger is On Demand,
and the AppleScript fired only On OCR rules. The rule's embedded script calls
DEVONthink's native `resolve DOI metadata`, falling back to `resolve book
metadata` when there is no DOI -- the same commands behind the two MCP tools
used here.

Enrichment is best-effort. A document that imported and OCR'd correctly must
not fail the pipeline because CrossRef or Open Library was unreachable.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from .mcp_client import MCPClient, MCPError

ENRICH_TIMEOUT = 60.0

# Opt-in only. Setting it sends this address to Unpaywall to look up an
# open-access PDF URL; CrossRef metadata works without it.
CONTACT_EMAIL_ENV = "UNPAYWALL_CONTACT_EMAIL"


def looks_like_isbn(value: str) -> bool:
    """True for an ISBN-10 or ISBN-13.

    DEVONthink's "ISSN/ISBN" custom field (identifier `is?n`) is shared: DOI
    resolution fills it with the journal's ISSN. Sending an ISSN to a book
    lookup would enrich the record with an unrelated book.
    """
    digits = re.sub(r"[\s-]", "", value)
    return bool(re.fullmatch(r"\d{9}[\dXx]|\d{13}", digits))


def enrich_record(
    client: MCPClient,
    uuid: str,
    database_uuid: str,
    *,
    rename: bool = False,
    report: Callable[[str], None] = print,
) -> str | None:
    """Fill bibliographic custom metadata from the record's DOI, else its ISBN.

    Returns "doi" or "isbn" for the identifier used, or None when nothing was
    enriched. Never raises.
    """
    try:
        props = client.call_tool(
            "get_record_properties", {"uuid": uuid, "database_uuid": database_uuid}
        )
    except MCPError as e:
        report(f"Bibliographic enrichment skipped; could not read record: {e}")
        return None

    custom = props.get("customMetadata") or {}
    doi = props.get("doi") or custom.get("doi")
    isbn = next(
        (v for v in (props.get("isbn"), custom.get("is?n")) if v and looks_like_isbn(str(v))),
        None,
    )

    try:
        if doi:
            args = {"uuid": uuid, "database_uuid": database_uuid, "doi": doi, "rename": rename}
            if email := os.environ.get(CONTACT_EMAIL_ENV):
                args["contact_email"] = email
            client.call_tool("resolve_doi_metadata", args, timeout=ENRICH_TIMEOUT)
            report(f"Enriched from DOI {doi}")
            return "doi"
        if isbn:
            client.call_tool(
                "resolve_book_metadata",
                {"uuid": uuid, "database_uuid": database_uuid, "isbn": isbn, "rename": rename},
                timeout=ENRICH_TIMEOUT,
            )
            report(f"Enriched from ISBN {isbn}")
            return "isbn"
    except MCPError as e:
        report(f"Bibliographic enrichment failed ({e}); continuing")
        return None

    report("No DOI or ISBN detected; bibliographic enrichment skipped")
    return None
