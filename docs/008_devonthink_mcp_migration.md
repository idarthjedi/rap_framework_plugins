# Plan: DEVONthink Import via the DEVONthink MCP Server

**Status**: Implemented

**Date**: 2026-09-21

## Context

`scripts/devonthink_importer.applescript` is 436 lines, and a large fraction of it is not
business logic — it is scaffolding around AppleScript's limitations:

- **Apple Event timeouts.** AppleScript waits 120 s for a reply. OCR routinely exceeds that,
  aborting the *wait* with error `-1712` while DEVONthink keeps working. The script carries
  `with timeout` blocks, a `-1712` branch, and ~60 lines of orphan-adoption logic that exist
  only to clean up after this one failure mode.
- **Shelling out for basics.** SHA-256 via `do shell script "shasum"`, millisecond timestamps
  via `do shell script "python3 -c ..."`, PDF text stats via a third `do shell script`. Each is
  a subprocess; the last needs a `path to me` trick to locate its helper.
- **Untestable.** No test executes this script. `tests/` mocks nothing around `osascript`.

DEVONthink 4.4 now ships a **first-party MCP server** inside the app bundle
(`/Applications/DEVONthink.app/Contents/Library/LoginItems/DEVONthink MCP.app`), exposing 62
tools over a supported, documented interface. Migrating moves this step into ordinary Python —
testable, debuggable, and consistent with the rest of the pipeline, which is already
`python`/`command` typed.

**Outcome:** the DEVONthink step becomes a Python script with no AppleScript dependency, the
`-1712` failure class disappears by construction, and the recovery logic shrinks substantially.

---

## Research findings that shape this plan

Verified live against the running server and the repo:

| Finding | Evidence |
|---|---|
| Hash dedup works identically over MCP | `search_records(query="mdsourcehash:<sha>")` returned the exact expected record |
| `sourcehash` already exists as a `string` custom metadata field | `list_custom_metadata_fields` |
| Inbox addressing is path-based | inbox children report `location: "/Inbox/"`; `incomingGroupUUID` ≠ `rootUUID` |
| stdio session costs **31.5 ms**, zero dependencies | measured spike: spawn 1.2 ms + initialize 29.6 ms + call 0.7 ms |
| HTTP works, but only with a bearer token | unauthenticated POST → 401; `Authorization: Bearer <token>` → 200. `auth.required: false` does *not* disable an already-configured token |
| Server's own DEVONthink budget is **900 s** | `devonthink.replyTimeout: 900` in the MCP config |
| Server will launch DEVONthink on demand | `devonthink.launchIfNeeded: true` |
| **No smart-rule tool exists** | all 62 tool names checked — zero match `rule`/`trigger`/`perform` |
| HTTP mode is *not* stdio under the covers | PID 728 (launchd, `*:8420`) spawns no stdio children |
| `type: "python"` needs no executor changes | `executor.py` runs `[sys.executable, path] + args` |
| Return strings are never parsed | `pipeline.py` branches only on exit code; all four uses of `.output` are log calls |
| Nothing downstream reads the stamped hash | `devonthink`/`DNtp`/`osascript` = zero hits across `research_analysis_platform` and `rap_obsidian_utils`; RAP recomputes SHA-256 itself |
| But ~1,012 Obsidian notes carry a matching `sourcehash:` | independently computed by RAP; `docs/006` backfilled 107 records to keep the two in agreement |

### The 900-second finding

`replyTimeout: 900` is the single most consequential discovery. The MCP server owns the
long-running DEVONthink call internally, so the client never sees a 120 s Apple Event ceiling.
**The entire `-1712` defence — `with timeout` blocks, the timeout branch, late-OCR adoption —
becomes dead code.**

---

## Decisions taken

| Decision | Choice | Rationale |
|---|---|---|
| Transport | **stdio**, behind a seam | Both transports verified working. stdio wins on robustness: no runtime credential read, no dependency on the server being toggled on. 31.5 ms is noise vs OCR. HTTP stays a one-line config switch. |
| Smart rules | **Drop the call — it is a verified no-op** | Decode showed no enabled rule carries the `On OCR` bit. Nothing is being triggered, so nothing needs replicating. See below. |
| Bibliographic enrichment | **Add explicitly, via MCP** | Replaces the never-firing `Download Bibliographic Metadata` rule with direct `resolve_doi_metadata` / `resolve_book_metadata` calls. New behaviour, deliberately chosen. `rename` off by default. |
| OCR | **Preserved exactly** | `needs_ocr()` gating and the 1000 chars/page threshold carry over unchanged. Only the call shape changes. |
| Recovery | **Pre-stamp redesign** | Stamp `sourcehash` immediately after import, before OCR. Removes the orphan scan. |
| MCP client library | **Hand-rolled, stdlib only** | Proven by spike. The `mcp` SDK would be the project's first heavy dependency (pydantic/httpx/anyio) for ~60 lines of framing. |
| Hash | **SHA-256 of the original file, pre-import** | Nothing downstream *reads* it, but RAP independently stamps the same value into ~1,012 Obsidian notes. Hashing the post-OCR copy inside the database would silently break that correspondence. |
| Old AppleScript | **Never deleted in this work** | Both entries coexist in `config.json`. Rollback is flipping one `enabled` flag. Removal is a separate decision, after the MCP path is proven in real use. |

---

## The pre-stamp redesign

The old ordering made a crash mid-OCR unrecoverable without scanning:

```
ocr file → (record appears, unstamped, invisible to hash search) → stamp
```

MCP splits this, so the UUID is known *before* OCR begins:

```
create_group_path       → destination group
import_file             → uuid                  record exists
set_record_custom_metadata(mode="merge")        ← STAMP HERE, findable immediately
ocr_record(uuid)        → searchable pdf        (only when needs_ocr() says so)
resolve_doi_metadata(uuid) / resolve_book_metadata(uuid)   ← enrichment
verify sourcehash still present → re-stamp if not           ← guard, see below
get_record_properties   → wordCount             verify searchable
```

### Spike results (2026-09-21) — these REVISE the design above

Run live against `Liberty.University/_MCPTest` with synthetic PDFs; all artifacts trashed after.

| Assumption in plan | Reality | Consequence |
|---|---|---|
| `ocr_record` converts in place | **Creates a new record, leaves the original** in the same group | Must trash the pre-OCR original after OCR, or every import leaves two records |
| `wordCount == 0` marks "not OCR'd" | Un-OCR'd image PDF reported `wordCount: 4` (DEVONthink light recognition on import) | Unusable as a signal. `kind` also useless — both read "PDF document" |
| — | Custom metadata **is copied** onto the OCR result | Pre-stamp survives OCR |
| Enrichment might wipe `sourcehash` | `resolve_doi_metadata` **merges** — stamp survived beside 15 new fields, name unchanged | Re-stamp guard is belt-and-braces, not load-bearing |
| — | Trashed records are **excluded** from `search_records` | Trashing the pre-OCR copy removes it from dedup |
| `"Inbox/…"` paths | `"/Inbox/_MCPTest"` created under the real incoming group (verified via `get_record_parents`) | Build path from the incoming group's actual name, not a literal |

**The replacement signal is byte-identity.** A pre-OCR import is an exact copy of the source:
`sha256(record file) == sourcehash` (verified: `3b47f704…` on both; OCR result `f6964312…`).
Because `sourcehash` is computed on the filesystem, outside DEVONthink, it names the *source
file*; comparing it to the record's current bytes answers "has DEVONthink transformed this
yet?" with no false positives.

### Revised flow

```
fresh import:  import_file → R0 (raw) → stamp → [needs_ocr] ocr_record(R0) → R1
               → trash R0 (guard: R0 still byte-identical) → enrich R1 → verify
```

Existing hits (trash excluded) are classified `raw` / `processed` by byte-identity:

| `needs_ocr` | Hits | Action | Returns |
|---|---|---|---|
| — | none | fresh import | `success` / `imported` |
| yes | ≥1 processed | trash raw leftovers; ensure processed is in dest (replicate if not) | `recovered` if anything was trashed, else `replicated` |
| yes | raw only | OCR a raw hit, trash raws, ensure in dest | `recovered` |
| no | any | nothing to repair; ensure in dest | `replicated` |

**Safety invariant, enforced in the applier independently of the decision logic:** a record is
only ever trashed if its file is byte-identical to the source *at the moment of trashing*. Any
record you've annotated has different bytes and can never be trashed.

**Known edge:** if `ocr_record` times out client-side, DEVONthink keeps working and may land R1
later, beside R0. Matching today's behaviour, the run keeps R0 and returns `imported`; the
raw+processed pair is repaired the next time that file is seen.

**Known limitation of dropping the orphan scan.** Some legacy records carry no `sourcehash` at
all — `Liberty-Writing-an-Abstract` in `/APA7th/` is one, with entirely empty custom metadata,
predating the hashing work. Without the orphan lookup, re-dropping such a file into the watch
folder would import a second copy rather than adopting the existing record. In practice this is
narrow: files are archived to `_Archived/` after processing, so re-drops are deliberate, and
DEVONthink's native duplicate detection (`item:duplicated`) still flags the result. Accepted
rather than carrying the scan forward; if it bites, the fallback is ~15 lines
(`lookup_records(filename=...)` filtered to the destination group).

> **`mode="merge"` is mandatory.** `set_record_custom_metadata` defaults to *replace* and is
> annotated `destructiveHint: true` — the default would wipe `doi`, `authors`, `journal` and
> every other field on the record.

### The hash contract

Investigated because of a concern that downstream processes needed the stamped hash returned.
**They don't — but a different, more fragile coupling does exist.**

*Confirmed negatives:*
- The AppleScript never returns the hash. Every exit path returns a bare status string.
- `pipeline.py` never parses stdout — all four uses of `ExecutionResult.output` are log calls.
- There is no `{hash}` variable in `FileVariables`, so it could not be passed downstream anyway.
- `rap scholarly_assessment` never reads DEVONthink. The strings `devonthink`, `DNtp`,
  `osascript`, `applescript` return **zero** hits across `research_analysis_platform` and
  `rap_obsidian_utils`.
- Nothing queries `mdsourcehash:` outside this repo. No database, sqlite, or hash-keyed cache
  joins the two systems. `~/.cache/rap/markdown/<tool>/<sha256>.md` is RAP-internal and knows
  nothing about DEVONthink.

*The actual coupling — two independent computations of the same value:*

```
watch-folder PDF ──┬─► importer:  sha256 ─► DEVONthink custom metadata "sourcehash"
                   │
                   └─► rap:       sha256 ─► Obsidian note frontmatter "sourcehash:"
                        (functions/generic/file_utils.py:32, hashlib, 8KB chunks)
```

Neither reads the other. But **~1,012 notes in `~/Obsidian/Leadership` already carry
`sourcehash:`**, and `docs/006_sha256_hash_migration.md` states its purpose as making these agree
("an external system expects SHA-256") — backfilling 107 records to do so.

**Therefore, binding constraint on the port:**

> Hash **the original file at `{file_path}`, before `import_file`**, with SHA-256 over the raw
> bytes. Do **not** hash via `get_imported_record_path` — that returns the copy inside the
> `.dtBase2` package, which OCR rewrites, yielding a different digest.

Breaking this is silent: imports keep succeeding, records look correct, and the correspondence
with a thousand Obsidian notes quietly stops holding. No test anywhere would catch it, which is
why `test_hash_contract.py` is in the test list below.

*Optional, cheap, enables a stated goal:* emit `sourcehash=<hex>` on stdout. The pipeline logs
stdout line-by-line at INFO, so it costs nothing and makes the value visible for debugging — and
it is the missing piece for the "skip the RAP step if the hash already exists in Obsidian" idea,
which currently has no way to learn the hash. Not required by this migration.

### The enrichment/stamp ordering hazard

`resolve_doi_metadata` and `resolve_book_metadata` **write custom metadata themselves**
(`record_enriched: true` in their response). Whether they merge or replace is undocumented. If
they replace, they would wipe the `sourcehash` stamped moments earlier — and the failure is
invisible: the record looks fine, but every future import of that file becomes a duplicate
because the hash search no longer finds it.

Mitigation, cheap and unconditional: after enrichment, `get_record_custom_metadata(uuid)` and
re-stamp `sourcehash` if it is missing. One extra read per import. **This must be verified
explicitly during implementation** — see the verification section.

---

## Architecture

New package, thin CLI shim, **no changes to `executor.py`, `config.py`, or the schema**:

```
src/rap_importer_plugin/devonthink/
├── __init__.py
├── mcp_client.py      # stdlib stdio JSON-RPC client (the spike, hardened)
├── importer.py        # the port: parse → dedupe → import → stamp → OCR → enrich → verify
├── enrichment.py      # resolve_doi_metadata / resolve_book_metadata, non-fatal
└── errors.py          # exit-code mapping, preserving 1000–1008

scripts/devonthink_importer.py   # argv shim → importer.main(), injects pdf_text_stats
scripts/pdf_text_stats.py        # UNCHANGED, shared by both importers
```

`config.json` gains a **second** script entry. The AppleScript entry is never edited and never
deleted — it stays in the file as a working fallback. See "Rollout and rollback" below.

### Contract to preserve (`pipeline.py` depends on these)

- **Exit 0 = success.** Nothing parses stdout.
- **Failures write to stderr and exit non-zero** — this is the only way to stop the downstream
  Scholarly Assessment step from running on an unsearchable document.
- **`TIMING:`-prefixed stderr lines** are extracted and logged (`line.startswith("TIMING:")`).
- Keep emitting `success` / `imported` / `recovered` / `replicated` on stdout for log continuity.
- Stay under the outer 600 s `timeout`, which hard-kills and discards all output.

---

## Implementation steps

1. **`mcp_client.py`** — harden the spike: context manager, `initialize` handshake,
   `notifications/initialized`, `tools/call` with per-call timeout, JSON-RPC error → exception,
   stderr drain on a thread (prevents pipe-buffer deadlock on a chatty server), guaranteed
   `terminate()` on exit. Transport seam: `StdioTransport` / `HttpTransport` behind one
   `call_tool(name, args, timeout)` method.
2. **`pdf_text_stats.py` stays in `scripts/`** *(revised during implementation)*. Moving it
   would break the AppleScript fallback, which locates it with `path to me` and runs it under
   the system `python3` with no access to the package. Instead the importer takes the stats
   function as an injected callable, and `scripts/devonthink_importer.py` passes the sibling
   module in — one source of truth, called in-process, fallback untouched.
3. **`importer.py`** — port the logic:
   - `parse_path_components()` — direct port of `parsePathComponents`, same three cases
     (`db/file.pdf` → inbox; `db/Inbox/...` → inbox + subgroups; else → group path).
     Inbox subgroups become `create_group_path(location="Inbox/" + group_path)`.
   - `hashlib.sha256` replaces `shasum` — **of the original file at `{file_path}`, read before
     import**. See "The hash contract" below; this one is easy to get subtly wrong.
   - `needs_ocr()` — keep `skipOCRAboveCharsPerPage = 1000` and the calibration comment verbatim;
     that threshold is empirically derived and must not drift.
   - **OCR is preserved unchanged.** `ocr file ... to destGroup` becomes
     `import_file` → `ocr_record(uuid)`, still gated by `needs_ocr()`. The skip-when-already-
     searchable behaviour that keeps import times down is carried over exactly.
   - The pre-stamp sequence above, including the post-enrichment `sourcehash` guard.
   - **No smart-rule call** — the AppleScript's `perform smart rule` line is a verified no-op
     (see below); it is not carried over.
   - `wordCount == 0` → exit non-zero (preserves error 1008 semantics).
4. **Bibliographic enrichment** (new behaviour — see "Smart rules" below for why this is being
   added rather than triggered). After OCR, mirroring what the `Download Bibliographic Metadata`
   rule's embedded script does:
   - `resolve_doi_metadata(uuid=...)` — the tool extracts the DOI from the record itself.
   - On no DOI, fall back to `resolve_book_metadata(uuid=...)`, matching the rule's
     `if theDOI is not "" ... else ... ISBN` structure.
   - **`rename` defaults to `False`.** The stock rule uses `rename yes`, but that rule has never
     run, so enabling it would start rewriting names across a 2,493-record corpus on first
     contact. Ship it off, expose it as one config key, let it be turned on deliberately.
   - **`contact_email` stays empty**, matching the rule (`property theContactEmail : ""`). It
     only enables Unpaywall open-access PDF lookup, which means sending an email address to a
     third-party service — opt-in via `.env` if wanted, never defaulted.
   - Enrichment failure (no DOI, network error, CrossRef miss) is **non-fatal**: log and
     continue. A document that imports and OCRs correctly must not fail the pipeline because
     CrossRef was down.
   - Note `resolve_book_metadata` prefers Google Books, which now requires an API key;
     `resolvers.googleBooksAPIKey` is empty, so it transparently falls back to Open Library.
5. **`scripts/devonthink_importer.py`** — argv parsing, `TIMING:` emission to stderr, exit codes.
6. **`config.json`** — **add** a second script entry, "DEVONthink Import (MCP)", with
   `enabled: true`, and set the existing AppleScript entry to `enabled: false`. Leave that
   entry's `args`, `include_paths`, and `timeout` untouched so the fallback is byte-identical to
   today's working config. Add the enrichment toggles (`enrich_bibliographic`, `enrich_rename`).
   See "Rollout and rollback".
7. **Tests** — the first real coverage for this path:
   - `test_devonthink_path_parsing.py` — pure-function table test of all routing cases.
   - `test_mcp_client.py` — client against a fake stdio server (an echo script), covering
     handshake, error responses, timeout, and stderr drain.
   - `test_pdf_stats.py` — known-good fixtures across the calibration bands (0, ~40, ~90, >1000
     chars/page).
   - `test_enrichment.py` — fake client asserting DOI→book fallback order, and that an
     enrichment exception does not fail the import.
   - `test_hash_contract.py` — the guard for the constraint above. Assert the new implementation
     produces a byte-identical digest to `shasum -a 256` on the same fixture, **and** to RAP's
     `functions/generic/file_utils.py:calculate_file_hash`. This is the only automated defence
     of the Obsidian correspondence.
   - Live smoke test kept out of CI, marked `@pytest.mark.live`.
8. **Docs** — write `docs/008_devonthink_mcp_migration.md` (next free number; 007 is highest),
   recording the smart-rule no-op finding and the promotion sequence. In `CLAUDE.md`, **keep**
   the `osacompile` step and the DEVONthink AppleScript Reference section — the AppleScript is
   still live — and add an MCP section alongside them, noting which entry is active. Wiki:
   `DEVONthink-Integration.md`, `Pipeline-Configuration.md`.

---

## Verification

```bash
# 1. Unit tests
uv run python -m pytest tests/ -v

# 2. Client smoke test against the real server
uv run python -c "
from rap_importer_plugin.devonthink.mcp_client import MCPClient
with MCPClient() as c:
    print(c.call_tool('is_running', {}))
    print([d['name'] for d in c.call_tool('get_databases', {})])
"

# 3. Single-file dry run, comparing against the AppleScript's behaviour
uv run python scripts/devonthink_importer.py \
    ~/RAP/RAPPlatform-Import/Liberty.University/TEST/sample.pdf \
    "Liberty.University/TEST/sample.pdf" 300
echo "exit=$?"
```

Then verify in DEVONthink: record landed in `Liberty.University/TEST`, `sourcehash` is set,
and `wordCount > 0`.

**Enrichment / stamp-survival check — do this first, before trusting the pipeline.** This is the
one genuinely unknown interaction in the plan:

```bash
# on a scratch record with a known DOI, in a scratch group:
#   1. set sourcehash
#   2. call resolve_doi_metadata(uuid=...)
#   3. re-read custom metadata
# PASS: sourcehash still present alongside the new doi/journal/authors fields
# FAIL: sourcehash gone -> enrichment replaces rather than merges; the
#       post-enrichment re-stamp guard is then load-bearing, not belt-and-braces
```

Also confirm enrichment did **not** rename the record (`rename` defaults to `False`).

**Idempotency check** — re-run the same command. It must print `replicated`, exit 0, and create
no second copy.

**Crash-recovery check** — the property the redesign buys. Interrupt between `import_file` and
`ocr_record` (`kill -9` the script), then re-run: it must find the stamped record, observe
`wordCount == 0`, **re-OCR in place**, and print `recovered` — not `replicated`.

**End-to-end** — drop a real PDF into the watch folder with the daemon running:

```bash
uv run rap-importer --foreground --log-level DEBUG
```

Confirm the full four-script pipeline runs, `TIMING:` lines appear in the log, and the file is
archived to `_Archived/`.

---

## Rollout and rollback

**The AppleScript is not touched and not deleted.** `scripts/devonthink_importer.applescript`
and `.scpt` stay exactly as they are, and their `config.json` entry stays in the file. No step in
this plan removes either.

### The mechanism: two entries, one `enabled: true`

Both DEVONthink entries live in the `pipeline.scripts` array of the "RAP Research" watcher. The
per-script `enabled` flag selects which one runs.

Verified this actually works: `config.py:65` exposes `enabled_scripts` (filters on `s.enabled`),
and `pipeline.py:208` builds its run list from exactly that property. A disabled script is
skipped entirely — not executed, not path-filtered, not logged as failed.

```json
{
  "name": "DEVONthink Import (MCP)",
  "reqs": "DEVONthink 4; MCP server bundled with the app",
  "type": "python",
  "path": "scripts/devonthink_importer.py",
  "enabled": true,
  "args": ["{file_path}", "{relative_path}", "300"],
  "include_paths": ["Liberty.University/*"],
  "timeout": 600
},
{
  "name": "DEVONthink Import (AppleScript, fallback)",
  "reqs": "DEVONthink 4 (tested) must be running",
  "type": "applescript",
  "path": "scripts/devonthink_importer.scpt",
  "enabled": false,
  "args": ["{file_path}", "{relative_path}", "300"],
  "include_paths": ["Liberty.University/*"],
  "timeout": 600
}
```

**Rollback is flipping the two flags** — `false`/`true` instead of `true`/`false` — and
restarting the daemon. No code changes, no recompilation, no git revert. The AppleScript entry's
`args`, `include_paths`, and `timeout` are byte-identical to today's working config, so the
fallback path is the one already in production, not a reconstruction of it.

> **Never set both to `true`.** Pipeline scripts run **sequentially over the same file**, not as
> alternatives. Both enabled means every PDF gets processed twice: the first imports and stamps,
> the second finds that hash and calls `replicate_record`, leaving a stray replica in the
> destination group on every import. Exactly one enabled, always.

### Optional: canary by path during stage 1

If you want the new path exercised on real files without committing the whole corpus to it,
`exclude_paths` beats `include_paths` (deny-first, per CLAUDE.md), so disjoint routing lets both
run with a guaranteed-empty intersection:

```jsonc
// MCP entry:         "include_paths": ["Liberty.University/_MCPTest/*"]
// AppleScript entry: "include_paths": ["Liberty.University/*"],
//                    "exclude_paths": ["Liberty.University/_MCPTest/*"],  "enabled": true
```

Everything real keeps flowing through the AppleScript; only `_MCPTest/` drops exercise MCP. This
also enables a direct A/B — drop the same PDF into both locations and compare the two resulting
records on group placement, `sourcehash`, `wordCount`, and enrichment fields.

This is strictly optional. The simple flag flip above is the supported path.

### Promotion sequence

| Stage | MCP | AppleScript | Gate to advance |
|---|---|---|---|
| 1. Verify | `enabled: true`, canary path (or run by hand) | `enabled: true`, excludes canary | Test PDFs import, OCR, stamp, enrich correctly |
| 2. Live | `enabled: true`, full path | **`enabled: false`** | A few weeks of real coursework, no surprises |
| 3. Removal | — | *separate decision, not this work* | Your call, later |

**The AppleScript is never deleted in this plan.** `scripts/devonthink_importer.applescript` and
`.scpt` stay on disk, their config entry stays in the file, and no step here removes either.

---

## Resolved questions

- [x] **HTTP transport** — verified working (`HTTP 200` with bearer auth). Shipping as an
      available-but-not-default transport. Rejected as the default because the importer would
      have to read the token at runtime, and `--generate-bearer-token` / the AI ▸ MCP pane can
      rotate it — silently breaking a background daemon. stdio has no such coupling.
- [x] **Smart-rule replication** — not needed. The call is a no-op today. See below.

---

## Smart rules — the call is already a no-op

`devonthink_importer.applescript:168` runs:

```applescript
perform smart rule record theRecord trigger OCR event
```

DEVONthink's `trigger` parameter runs **only** rules registered for that event. `On OCR` is
bit `128`. Decoding the `KEVE` event bitmask out of every rule's `data` blob in
`~/Library/Application Support/DEVONthink/SmartRules.plist`:

| Rule | KEVE mask | Event |
|---|---|---|
| Reminders | `0x0000000000100000` | On Reminder |
| Filter Duplicates | `0x0000000000000001` | On Demand |
| Bates Numbering | `0x0000000000000001` | On Demand |
| Automatic Locking | `0x0000000000000001` | On Demand |
| Unify Date In Names | `0x0000000000000001` | On Demand |
| Download Bibliographic Metadata | `0x0000000000000001` | On Demand |
| Rename to Chat suggestion | `0x0000000000000001` | On Demand |
| Notification with Summary via Chat | `0x0000000000000001` | On Demand |
| Apply Chat suggestions | `0x0000000000000001` | On Demand |

**Rules with the `On OCR` bit: none. With `On Import`: none.** `On Demand` rules run only when
`trigger` is *omitted*, or via Tools ▸ Apply Rules.

So the line matches nothing and has never done anything. **Dropping it during the migration is
exactly behaviour-preserving** — which also means the absence of a smart-rule tool among the
server's 62 tools costs this migration nothing.

Note: populated `doi` fields on existing records are **not** evidence the bibliographic rule
ran. DEVONthink 4 extracts DOI/ISBN natively on import — that is why `search_records` returns
`doi` on its brief record shape.

### Do not confuse this with OCR itself

`trigger OCR event` names the *event smart rules subscribe to*; it does not perform OCR. OCR is
the separate `ocr file filePath to destGroup` call at line 128, gated by `needsOCR` at lines
238–265. **That has always run and is fully preserved** — including the
`skipOCRAboveCharsPerPage : 1000` threshold added to keep import times down on born-digital
PDFs. Only line 168 is dead.

### Decision: replace the dead rule with explicit enrichment

Rather than leave the intended behaviour missing, the one rule that matters for a research
corpus — `Download Bibliographic Metadata` — is reimplemented directly in the importer. Its
embedded script decompiles to:

```applescript
resolve DOI metadata theDOI record theRecord apply yes rename yes contact email theContactEmail
-- else, when no DOI:
resolve book metadata theISBN record theRecord apply yes rename yes
```

Those are the native commands behind `resolve_doi_metadata` and `resolve_book_metadata`, so the
mapping is near 1:1. Implementation detail is in step 4 above.

The remaining eight rules are **not** reimplemented: they are `On Demand` by design (run
manually via Tools ▸ Apply Rules), and several depend on things MCP cannot reach anyway —
Display Notification, Play Sound, DEVONthink's internal Bates counter, and the `%placeholder%`
template engine.

If you later want the rules themselves to fire, the promising route is ticking **On OCR** on the
wanted rules and testing whether `ocr_record` makes DEVONthink raise the event natively — no MCP
smart-rule tool required. **Unverified**, and out of scope here.

---

## Repo hygiene to clear first

Surfaced during investigation, both worth resolving before migrating:

- `scripts/.devonthink_importer.scpt.swp` — a stray vim swap file.
- `scripts/devonthink_importer.scpt` (Sep 20 19:09) is **newer than** its source
  `scripts/devonthink_importer.applescript` (Sep 6 12:56), and is currently modified in git.
  Decompiling the `.scpt` and diffing shows the difference is whitespace only, so the two are
  semantically in sync — but a compiled artifact ahead of its source means the `.scpt` was
  edited directly at some point. Confirm before treating the `.applescript` as the thing to
  port, and commit or revert the working-tree change either way.
