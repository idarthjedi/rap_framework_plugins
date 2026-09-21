# Plan: MCP Importer Reconciliation Fix (Incident 2026-09-21)

**Status**: Implemented

**Date**: 2026-09-21

Follows `docs/008_devonthink_mcp_migration.md`, whose reconciliation design this replaces.

## Context

After switching the pipeline to the MCP importer (PR #1, branch `feat/devonthink-mcp-importer`),
a batch of HBR PDFs appeared to fail: DEVONthink logged "password-protected / cannot be OCR'd",
the files vanished into `_Archived` with unchanged dates, and no new DEVONthink records were
visible. The concern was that the MCP path fails to import where the AppleScript succeeded, and
that documents were being lost.

Investigation used only the two logs, read-only hashing of files in the *import* folder, and
read-only MCP calls. Nothing inside the DEVONthink database package was read or touched.

**Outcome of investigation: no document was lost or failed to import.** But it exposed one
critical bug that was a single successful OCR away from replacing curated records, plus three
lesser defects. This plan fixes them and adds a repeatable, read-only audit.

---

## Diagnosis

### Every file is accounted for

| Stage | Count | Evidence |
|---|---|---|
| PDFs processed by the MCP importer today | 25 | 25 `completed in` lines, 25 distinct `sourcehash=` values in `~/Library/Logs/rap-importer.log` |
| Archived today | 25 | `find _Archived -newerct 2026-09-21` → 25 (a move changes inode-change time, not mtime) |
| Archived file ↔ logged hash | 25/25 | SHA-256 of each archived file matches a logged hash |
| Live in DEVONthink (not trashed) | 25/25 | `search_records("mdsourcehash:…")`, trash excluded; all `PDF+Text` |
| Left unprocessed in import folder | 0 | no PDFs outside `_Archived` / `EndNote` |
| Pipeline failures | 0 | no errors; every run exited 0 |

### Why they looked missing

1. **20 of 25 were already in DEVONthink from 9/19** (imported by the AppleScript). The importer
   correctly matched them by hash and did not create second copies — but **logged `imported`**,
   implying new records. (The AppleScript would have logged `replicated`.)
2. **DEVONthink had renamed them to article titles** (`H099N1-PDF-ENG` → *"The Science of
   Developing Creative Talent"*), so searching by HBR code finds nothing. Today's `H00OF8` was
   likewise renamed on import to *"How to Make Yourself Work When You Just Don't Want To"*.
   This is DEVONthink naming records from the PDF's embedded title — not the importer
   (enrichment ran with `rename=False` and found no DOI).
3. **The password-protected OCR failures are not new.** DEVONthink's log shows identical failures
   for the same HBR files on 9/19 under the AppleScript. Both importers keep the document without
   OCR. These PDFs are born-digital with full text layers (1,100–4,800 words each), so OCR was
   never needed.

### Bugs found

**B1 — CRITICAL: reconciliation would replace pre-existing, curated records.**
For the 20 pre-existing records, `decide_existing()` saw "raw" hits (file byte-identical to the
source) with OCR wanted, and planned: *OCR the record, then trash the original* — the
crash-recovery path. OCR failed only because the PDFs are password-protected. Had it succeeded,
each record would have been replaced by a copy with a **new UUID**, breaking
`x-devonthink-item://` links, dropping replicas in other groups, and sending the curated original
to the trash.

Root cause: the design assumed *byte-identical ⇒ untouched crash leftover*. That is false.
Renaming, tagging, custom metadata (these records carry title names, 5 tags, author, abstract,
company, date) and replication never change a PDF's bytes. And records the AppleScript
deliberately imported without OCR (password-protected, OCR timeout) are finished imports, not
leftovers. Verified: all 20 records have `modificationDate` ≤ 9/19 — unchanged today.

**B2 — Misleading status.** An existing record whose OCR attempt failed was reported as
`imported`. This is the direct cause of the "failed to import" impression.

**B3 — Encrypted PDFs misrouted to OCR.** `pdf_text_stats` cannot decompress encrypted content
streams, so born-digital password-protected PDFs read as 0 chars/page → "needs OCR" → a doomed
OCR attempt on every import. Harmless on its own, but it is what drove B1's plan.

**B4 — Latent: fresh path trusts `ocr_record`'s returned UUID.** `_import_new` trashes the
original whenever `ocr_record` returns *any* UUID. If the server ever returned the input record
(no new copy), the only copy would be trashed. Not observed, but unguarded.

---

## Recovery

**Nothing to restore.** The one real trash today (`wang-et-al-2026…`, 15:15:50) is the pre-OCR
copy of a successful OCR; its OCR'd replacement `B8B98902…` is live in `/BUSI610/`. All other
"Trashed by MCP" entries are this session's test artifacts in `_MCPTest`.

**Do not empty the DEVONthink trash** until the fixes land — it is the safety net if anything
here turns out to be wrong.

The file → record mapping (the list requested). Every row is joined **by hash**: the SHA-256 of
the archived file equals the `sourcehash` read back from the DEVONthink record. All records are
in `/Harvard Business Review/` except `wang-et-al`, in `/BUSI610/`.

| Archived file | DEVONthink record | Record UUID | Added | hash |
|---|---|---|---|---|
| **Imported today (5)** | | | | |
| BUSI610/wang-et-al-2026-…-job-postings.pdf | wang-et-al-2026-remote-work-and-hiring-requirements-… (OCR'd) | B8B98902… | 9/21 15:15 | 5a71a5d0 |
| H00OF8-PDF-ENG.pdf | How to Make Yourself Work When You Just Don't Want To | 34EB824B… | 9/21 12:41 | ab12fb5e |
| H04XBL-PDF-ENG.pdf | H04XBL-PDF-ENG | 6CF30D47… | 9/21 15:14 | fa16878b |
| H03ZQ5-PDF-ENG.pdf | H03ZQ5-PDF-ENG | 7AA3734F… | 9/21 15:18 | b0a4cb4b |
| H04FN5-PDF-ENG.pdf | H04FN5-PDF-ENG | 69CAF79B… | 9/21 15:18 | 2454937f |
| **Already in DEVONthink since 9/19, unchanged today (20)** | | | | |
| H026IA-PDF-ENG (1).pdf | How to Tell Someone They're Being Laid Off | 285EF49B… | 9/19 | f612a75a |
| H047AT-PDF-ENG.pdf | Why Your Inner Circle Should Stay Small, and How to Shrink It | B53F8B78… | 9/19 | 724289bf |
| H048GE-PDF-ENG.pdf | How Being a Workaholic Differs from Working Long Hours … | E7D2B2BD… | 9/19 | e2d3de74 |
| H04L2I-PDF-ENG.pdf | How to Blow a Presentation to the C-Suite | 5F8DFC6D… | 9/19 | 02da3ff7 |
| H04QUS-PDF-ENG.pdf | Why Money Manages Us: A Historical Perspective | 293DB226… | 9/19 | c567b5f1 |
| H04RCY-PDF-ENG-000.pdf | What PwC Learned from Its Policy of Flexible Work for Everyone | D9EE966D… | 9/19 | 4a8c71d6 |
| H05RST-PDF-ENG.pdf | How to (Actually) Change Someone's Mind: Three Strategies … | 57A2EFF0… | 9/19 | 366b0688 |
| H05SWX-PDF-ENG.pdf | How to Reimagine the Second Half of Your Career | 433A9E1B… | 9/19 | 9605d670 |
| H061WP-PDF-ENG.pdf | Why Capable People Are Reluctant to Lead | 33639EA6… | 9/19 | 5245f881 |
| H06NCN-PDF-ENG.pdf | 5 Things High-Performing Teams Do Differently | 8F2DA89C… | 9/19 | 9322659d |
| H07QA5-PDF-ENG.pdf | Why Kindness at Work Pays Off | 5D87934C… | 9/19 | 0d09dffb |
| H07WH8-PDF-ENG.pdf | How to Become a Better Strategic Thinker | D71585FC… | 9/19 | 7b215576 |
| H0813H-PDF-ENG (1).pdf | Surveilling Employees Erodes Trust - and Puts Managers in a Bind | 47C13ABF… | 9/19 | 3d25542d |
| H08EYN-PDF-ENG.pdf | Why Gaming Is Good for the Workplace | 33B23C0C… | 9/19 | f654edef |
| H08S2Z-PDF-ENG.pdf | Organizations Aren't Ready for the Risks of Agentic AI | A0268C2D… | 9/19 | 0b0ee1e8 |
| H0981H-PDF-ENG.pdf | AI Adoption Is Overloading Your Middle Managers | FE23737B… | 9/19 | efc4d31a |
| H099N1-PDF-ENG.pdf | The Science of Developing Creative Talent | A3C8BD54… | 9/19 | ad0221cd |
| R1905H-PDF-ENG.pdf | A New Approach to Contracts: Building Better Long-Term Strategic Partnerships | 7A2AA640… | 9/19 | a49fe5bb |
| R2101L-PDF-ENG.pdf | Managing Yourself: How to Help (Without Micromanaging) | 74A30D1B… | 9/19 | ff2230a0 |
| R2605B-PDF-ENG.pdf | AI Is Revolutionizing Strategic Decision-Making | 4233CFFD… | 9/19 | a7ced789 |


---

## The pass/fail contract

The importer runs under a menu-bar app with exactly two outcomes the user ever sees:

| Outcome | What the user sees | What happens to the file |
|---|---|---|
| **PASS** (exit 0) | nothing (`on_success: false`) | archived to `_Archived` |
| **FAIL** (exit ≠ 0) | notification *"Import Failed: `<file>`: `<message>`"*; after 3 tries, *"Import Failed Permanently"* | **stays in the import folder** |

A stdout "WARNING" reaches only the log file — nobody sees it. So there are no warnings in the
design: every situation is classified as either **safe to pass** or **must fail**. The rule:

> **If proceeding could lose content or attach the file to the wrong record, FAIL.** A failure is
> visible and non-destructive — the file stays put and nothing in DEVONthink changes.

| Situation | Outcome |
|---|---|
| New file, OCR succeeds | PASS `success` |
| New file, OCR skipped (born-digital, encrypted) or fails, document searchable | PASS `imported` (as the AppleScript did) |
| New file, document ends up with no text at all | FAIL 1008 (unchanged) |
| Hash matches a record that is **verified** to be the same document | PASS `replicated` (replicate into the destination, or no-op if already there) |
| Hash matches, but **no** matching record can be verified as the same document | **FAIL 1012**, nothing replicated, nothing imported |
| MCP server unreachable, stamp cannot be written | FAIL (unchanged) |

Messages are written for a notification banner: short, naming the file and the record.

---

## Fix plan

All on the existing branch, pushed to PR #1 before it merges. The pipeline stays on the MCP
importer throughout.

### 1. B1 — matched records are replicated, never modified, and only when verified

**What happened today, concretely.** You dropped `H099N1-PDF-ENG.pdf`. Its SHA-256 matched the
9/19 record *"The Science of Developing Creative Talent"*. DEVONthink had renamed that record and
it carries 5 tags plus author/abstract/company/date metadata — but none of that alters the PDF's
bytes, so its file still hashed identically to your dropped file. My code read "byte-identical"
as "an import that crashed before OCR", and because the encrypted PDF reads as 0 chars/page it
wanted OCR. Its plan: OCR the 9/19 record, then trash it.

**What would have happened if OCR had worked.** DEVONthink creates the OCR'd copy as a *new
record with a new UUID*. The importer would then trash the 9/19 record — and with it every
`x-devonthink-item://` link pointing at it, every replica in other groups (a replica is the same
record, so all instances go), and anything DEVONthink does not copy onto the new record. It would
have reported `recovered`: a PASS, silent. Only the password protection prevented this.

**The fix — the matched-record path can no longer change a record's content or existence.**
Given hash hits, the importer does only this:

1. **Verify identity** of each hit. A hit counts as *the same document* only if all hold:
   - **Exact stamp**: its stored `sourcehash` equals the incoming hash, read back and compared
     byte for byte — not trusting the search operator's matching.
   - **It is a PDF**: a record converted to another format, or a summary that inherited custom
     metadata, carries the stamp but is not the document.
   - **Content check**:
     - file byte-identical to the incoming file → *certainly* the same document; or
     - file differs (OCR'd, annotated) → its **page count must equal** the incoming file's.
       OCR and annotation never change page count; a merged, split or replaced document does.
       If either page count cannot be determined, the hit is *not* verified.
2. **Pick** among verified hits: one already in the destination group first, then the oldest.
3. **Replicate** it into the destination if it isn't there, then read its parents back to
   confirm → PASS `replicated`.
4. **No verified hit → FAIL 1012**, e.g. *"H099N1-PDF-ENG.pdf matches 'The Science of Developing
   Creative Talent' by hash but its page count differs (6 vs 9) — not filed"*. Nothing is
   replicated or imported, and the file stays in the import folder for you to look at.

Deliberately **not** done any more: OCR'ing an existing record, trashing an existing record, or
"repairing" what looks like an interrupted run. If a genuine interrupted run ever leaves an
un-OCR'd record, it simply stays un-OCR'd and gets replicated — the same result as the
AppleScript's OCR-timeout fallback. Duplicate stamps are reported by the audit script (step 5)
rather than acted on.

**Structural guard, independent of the logic above:** `trash_record` may only be called on a
UUID created by `import_file` *in the same run*, tracked in `self._created_this_run`. Anything
else raises before the call is made, which fails the run. `Plan.ocr` and `Plan.trash` are
removed, so the matched-record path cannot even express a destructive action.

### 2. B4 — never trash the only copy (`_import_new`)
The fresh-import path trashes its own pre-OCR original only when `ocr_record` returned a **different**
UUID that is live and carries the stamp (one `get_record_custom_metadata` call). Otherwise it
keeps the original exactly as if OCR had failed.

No warning is needed, because the case folds into existing pass/fail logic: the original is
kept, and the searchability check decides the outcome — PASS `imported` if the document has
text, FAIL 1008 if it has none. In neither case is anything lost.

### 3. B2 — truthful status, and where it went
- The matched-record path reports `replicated`, never `imported`.
- Each run adds `record=<uuid>` and `name="<name>" location=<location>` to its log lines.
  These are log-only (not shown to the user), but they mean the importer log answers "where did
  it go" directly, including DEVONthink's title renames.

### 4. B3 — skip OCR for encrypted PDFs (`importer.py`)
Add `is_encrypted(path)` (an `/Encrypt` entry in the trailer or cross-reference stream). When
true, skip OCR — it always fails for these — and treat the result as "OCR skipped". Lives in the
MCP importer; `scripts/pdf_text_stats.py` stays untouched for the AppleScript. The searchability
check still fails an encrypted document that has no text layer.

### 5. Read-only audit: `scripts/audit_imports.py`
The on-demand place to see what the menu bar can't show.
`uv run python scripts/audit_imports.py [folder] [--since YYYY-MM-DD]` hashes every PDF in a
folder (default `_Archived`; `--since` selects files moved there on or after a date, via
inode-change time) and reports per file: **found live** / **found only in trash** / **not in
DEVONthink** / **ambiguous** (hash matches but identity check fails), with record name, UUID,
location, kind and word count, plus any hash carried by more than one live record.
Uses only read-only MCP tools, enforced by an allowlist in the script that rejects any other
tool name — it cannot modify DEVONthink.

### 6. Tests (`tests/test_devonthink_importer.py`)
- **B1 regression**: a pre-existing, renamed, tagged record with metadata, byte-identical to the
  source, OCR wanted → no `ocr_record`, no `trash_record`, UUID and metadata unchanged, `replicated`.
- Identity checks: stamp mismatch, non-PDF hit, processed hit with a different page count, and
  unknown page count each make the hit unverified; with no verified hit → FAIL 1012 and no
  mutating calls.
- Processed hit with an equal page count → verified → replicated.
- `trash_record` on a UUID not created this run raises before the call.
- **B4**: `ocr_record` returning the input UUID, or an unstamped copy → original kept, not trashed.
- **B3**: encrypted PDF → no `ocr_record` call.
- **B2**: stdout carries `replicated`, `record=`, `name=`, `location=`.
- Remove the old recovery tests that asserted OCR-and-trash of existing records.
- Audit script: the allowlist rejects mutating tools; classification of live / trashed / missing
  / ambiguous against the fake.

### 7. Docs
- New `docs/009_mcp_importer_reconciliation_fix.md` (docs/008 is Implemented and immutable):
  this incident, B1's root cause, the pass/fail contract, the new invariants.
- `CLAUDE.md` "not obvious" list: byte-identity does not mean untouched; the importer only ever
  trashes what it created in the same run; hash matches must pass identity checks or the run
  fails; encrypted PDFs skip OCR; DEVONthink renames records from PDF titles.
- Wiki `DEVONthink-Integration.md`: replace the Recovery section (which currently promises
  OCR-and-trash of interrupted runs), add the pass/fail table, `record=` output, and the audit
  script.

---

## Verification

1. `uv run python -m pytest tests/ -q` — all pass, including the new regression tests.
2. **B1, live, in `_MCPTest` only**, with synthetic PDFs (never an HBR file, so no real record is
   touched): import a scanned PDF via `import_file`, stamp it, then rename it, tag it and set
   custom metadata (simulating curation). Run the importer on the same file into a *different*
   `_MCPTest` subgroup → expect PASS `replicated`, no `ocr` in TIMING, same UUID, name / tags /
   metadata intact, nothing new in DEVONthink's "Trashed by MCP" log.
3. **Ambiguous match, live**: stamp a synthetic record with the hash of a *different* synthetic
   PDF that has a different page count, then import that PDF → FAIL 1012 with a readable
   message, the file left in place, and no replicate or import in DEVONthink.
4. **B3, live**: an encrypted synthetic PDF (built in the scratchpad with `uv run --with pypdf`)
   → PASS `imported`, no OCR attempt in DEVONthink's log.
5. **Fresh-import regression**: scanned and born-digital synthetic PDFs → `success` / `imported`,
   one live record each, as before.
6. **Audit**: `scripts/audit_imports.py --since 2026-09-21` → 25 files, 25 found live, matching
   the Recovery table.
7. **In production**, after merge: drop one previously imported HBR file → PASS, the log shows
   `replicated` with its title name and location, and the DEVONthink record is unchanged.

---

## Implementation notes (2026-09-21)

Changes made while implementing, beyond the plan as approved:

- **No reads of the DEVONthink database at all.** The plan's identity check included "file
  byte-identical to the incoming file", which needs the record's file inside the `.dtBase2`
  package. Per the rule that nothing may touch DEVONthink's filesystem, that branch was dropped,
  along with the old `_is_raw()` / `_trash_raw()` hashing, which *did* read those files. Identity
  is now judged from MCP properties alone: exact `sourcehash`, `type == pdf`, equal page count.
  Page count covers the raw and OCR'd cases alike. The importer never calls
  `get_imported_record_path`, and the test fake deliberately lacks that tool.
- **Page count and encryption come from macOS CoreGraphics** (`devonthink/pdf_info.py`, stdlib
  `ctypes`), read from the incoming file. `pdf_text_stats` reports 0 chars/page for encrypted
  PDFs, and CoreGraphics reports their encryption and true page count: all 20 HBR records verify.
- **Failure banner.** A live run showed the "Import Failed" notification would begin with
  `TIMING:` lines, because the pipeline passes all of stderr to the notification. The importer
  now buffers its timing lines and, on failure, writes the reason first. (The AppleScript fallback
  has the same flaw; unchanged.)
- `UNSAFE_PLAN` (1010) became `UNSAFE_TRASH`; 1012 `AMBIGUOUS_MATCH` and 1013
  `REPLICATE_FAILED` are new. The audit's allowlist is four read-only tools; the trash is read by
  listing it, since the hash search cannot see trashed records even when scoped to the trash group.

**Full-archive audit** (all 649 files in `_Archived`, read-only, 3.7 s): 599 found, 46 trashed,
3 duplicate, 1 missing. None involve today's runs; they reflect earlier manual cleanup.
