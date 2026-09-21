"""In-memory stand-in for the DEVONthink MCP tools, shared by the importer and audit tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from rap_importer_plugin.devonthink.mcp_client import MCPTimeout, MCPToolError

# Tools that change DEVONthink. Tests assert these are never called where no
# change is allowed.
MUTATING = {
    "import_file",
    "ocr_record",
    "trash_record",
    "replicate_record",
    "move_record",
    "update_record",
    "set_record_custom_metadata",
    "set_record_tags",
    "create_group_path",
}


class FakeDevonthink:
    """Mirrors behaviour measured against the real server.

    - ocr_record makes a NEW record (custom metadata copied) and leaves the original.
    - Trashed records drop out of search, even when the search is scoped to the trash.
    - An un-OCR'd image PDF still reports a few words (DEVONthink's light text recognition).

    It deliberately has no get_imported_record_path: the importer must never
    touch files inside a DEVONthink database, so any attempt fails the test.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.store = tmp_path / "Files.noindex"
        self.store.mkdir()
        self.database = {
            "uuid": "DB",
            "name": "Liberty.University",
            "rootUUID": "DB",
            "incomingGroupUUID": "INBOX",
            "trashGroupUUID": "TRASH",
        }
        self.groups: dict[str, str] = {"/": "DB", "/Inbox": "INBOX"}  # location -> uuid
        self.group_props = {"INBOX": {"uuid": "INBOX", "location": "/", "name": "Inbox", "type": "group"}}
        self.records: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.ocr_behaviour = "ok"  # ok | timeout | error | same | unstamped
        self.replicate_behaviour = "ok"  # ok | noop
        self.raw_word_count = 4
        self.import_pages = 1
        self._next = 0

    # -- arranging state --

    def group(self, location: str) -> str:
        return self._create_group_path(location=location, database_uuid="DB")["uuid"]

    def add_record(
        self,
        source: Path,
        group: str,
        *,
        stamp: str | None,
        processed: bool = False,
        pages: int = 1,
        record_type: str = "pdf",
        name: str | None = None,
        added: str = "2026-01-01",
        metadata: dict[str, Any] | None = None,
        trashed: bool = False,
    ) -> str:
        uuid = self._uuid()
        path = self.store / f"{uuid}.pdf"
        path.write_bytes(source.read_bytes() + (b"%OCR-TEXT-LAYER" if processed else b""))
        meta = dict(metadata or {})
        if stamp:
            meta["sourcehash"] = stamp
        self.records[uuid] = {
            "path": path,
            "parents": {group},
            "meta": meta,
            "words": 18 if processed else self.raw_word_count,
            "trashed": trashed,
            "added": added,
            "pages": pages,
            "type": record_type,
            "name": name or source.stem,
        }
        return uuid

    def live(self) -> dict[str, dict[str, Any]]:
        return {u: r for u, r in self.records.items() if not r["trashed"]}

    def called(self, tool: str) -> list[dict[str, Any]]:
        return [args for name, args in self.calls if name == tool]

    def mutations(self) -> set[str]:
        return MUTATING & {name for name, _ in self.calls}

    def _uuid(self) -> str:
        self._next += 1
        return f"REC{self._next}"

    def _props(self, uuid: str) -> dict[str, Any]:
        if uuid in self.group_props:
            return self.group_props[uuid]
        rec = self.records[uuid]
        location = next(
            (loc.rstrip("/") + "/" for loc, g in self.groups.items() if g in rec["parents"]), "/"
        )
        props = {
            "uuid": uuid,
            "name": rec["name"],
            "type": rec["type"],
            "kind": "PDF+Text" if rec["type"] == "pdf" else rec["type"].title(),
            "pageCount": rec["pages"],
            "wordCount": rec["words"],
            "customMetadata": dict(rec["meta"]),
            "location": "/Trash/" if rec["trashed"] else location,
            "additionDate": rec["added"],
        }
        if "doi" in rec:
            props["doi"] = rec["doi"]
        return props

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

    def _get_record_properties(
        self, uuid: str | None = None, uuids: list[str] | None = None, database_uuid: str | None = None
    ) -> dict[str, Any]:
        if uuids is not None:
            return {"results": [self._props(u) for u in uuids]}
        if uuid not in self.records and uuid not in self.group_props:
            raise MCPToolError(f"get_record_properties: Record not found: {uuid}")
        return self._props(uuid)

    def _import_file(self, path: str, database_uuid: str, destination: str) -> dict[str, Any]:
        uuid = self._uuid()
        copy = self.store / f"{uuid}.pdf"
        shutil.copyfile(path, copy)
        self.records[uuid] = {
            "path": copy, "parents": {destination}, "meta": {}, "words": self.raw_word_count,
            "trashed": False, "added": "2026-09-21", "pages": self.import_pages, "type": "pdf",
            "name": Path(path).stem,
        }
        return {"uuid": uuid}

    def _set_record_custom_metadata(self, uuid: str, metadata: dict[str, Any], mode: str = "replace") -> dict[str, Any]:
        rec = self.records[uuid]
        rec["meta"] = {**rec["meta"], **metadata} if mode == "merge" else dict(metadata)
        return {"uuid": uuid, "metadata": dict(rec["meta"]), "dropped_fields": []}

    def _get_record_custom_metadata(self, uuid: str, database_uuid: str | None = None) -> dict[str, Any]:
        if uuid not in self.records:
            raise MCPToolError(f"get_record_custom_metadata: Record not found: {uuid}")
        return dict(self.records[uuid]["meta"])

    def _search_records(self, query: str, database_uuid: str | None = None, fields: list[str] | None = None,
                        group_uuid: str | None = None) -> dict[str, Any]:
        # Deliberately lenient (substring), so the importer's exact-stamp check is exercised.
        wanted = query.removeprefix("mdsourcehash:")
        hits = [
            {"uuid": u, "additionDate": r["added"]}
            for u, r in self.live().items()
            if wanted in str(r["meta"].get("sourcehash", ""))
        ]
        return {"results": hits, "total": len(hits)}

    def _get_record_parents(self, uuids: list[str], database_uuid: str) -> dict[str, Any]:
        return {"results": [
            {"uuid": u, "parents": [{"uuid": g} for g in self.records[u]["parents"]]} for u in uuids
        ]}

    def _get_record_children(self, uuid: str, offset: int = 0, limit: int = 1000) -> dict[str, Any]:
        if uuid == "TRASH":
            items = [{"uuid": u, "type": r["type"]} for u, r in self.records.items() if r["trashed"]]
        else:
            items = [{"uuid": u, "type": r["type"]} for u, r in self.live().items() if uuid in r["parents"]]
        page = items[offset : offset + limit]
        return {"items": page, "count": len(page), "total": len(items), "offset": offset}

    def _ocr_record(self, uuid: str) -> dict[str, Any]:
        if self.ocr_behaviour == "timeout":
            raise MCPTimeout("No reply within 300s")
        if self.ocr_behaviour == "error":
            raise MCPToolError("ocr_record: Unable to create an OCR document")
        if self.ocr_behaviour == "same":
            return {"uuid": uuid}
        original = self.records[uuid]
        new = self._uuid()
        path = self.store / f"{new}.pdf"
        path.write_bytes(original["path"].read_bytes() + b"%OCR-TEXT-LAYER")
        self.records[new] = {
            **original,
            "path": path,
            "parents": set(original["parents"]),
            "meta": {} if self.ocr_behaviour == "unstamped" else dict(original["meta"]),
            "words": 18,
            "added": "2026-09-21",
        }
        return {"uuid": new}

    def _trash_record(self, uuid: str, database_uuid: str) -> str:
        self.records[uuid]["trashed"] = True
        return "Record moved to trash"

    def _replicate_record(self, uuid: str, destination: str, database_uuid: str) -> dict[str, Any]:
        if self.replicate_behaviour == "ok":
            self.records[uuid]["parents"].add(destination)
        return {"uuid": uuid, "destination_uuid": destination}

    def _resolve_doi_metadata(self, uuid: str, database_uuid: str, doi: str, rename: bool, **_: Any) -> dict[str, Any]:
        self.records[uuid]["meta"].update({"doi": doi, "journal": "Personnel Psychology"})
        return {"record_enriched": True}
