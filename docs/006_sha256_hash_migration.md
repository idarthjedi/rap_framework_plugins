# Plan: Migrate sourceHash from SHA-1 to SHA-256

**Status**: Implemented (2025-12-31)

## Problem Statement
The AppleScript (`devonthink_importer.applescript`) uses SHA-1 for the `sourceHash` custom metadata field in DEVONthink, but an external system expects SHA-256 hashes. Need to:
1. Update the AppleScript to use SHA-256 going forward
2. Migrate existing records in DEVONthink from SHA-1 to SHA-256

## Scope
- **Archived files location**: `/Users/darthjedi/Documents/RAPPlatform-Import/_Archived/Liberty.University/Harvard Business Review/`
- **DEVONthink database**: Liberty.University only
- **External system**: Requires SHA-256 hashes for consistency
- **Script retention**: Keep migration script permanently for future use

---

## Implementation Plan

### Step 1: Update AppleScript for SHA-256 (Going Forward)

**File**: `scripts/devonthink_importer.applescript`

**Change**: Line 86 - Update the `shasum` command:
```applescript
-- Before (SHA-1)
set hashResult to do shell script "shasum -a 1 " & quoted form of filePath & " | awk '{print $1}'"

-- After (SHA-256)
set hashResult to do shell script "shasum -a 256 " & quoted form of filePath & " | awk '{print $1}'"
```

**Also update**: Comment on line 84:
```applescript
-- Before
-- Calculate SHA-1 hash of file using shasum command

-- After
-- Calculate SHA-256 hash of file using shasum command
```

**Recompile**: After editing, compile the updated script:
```bash
osacompile -o scripts/devonthink_importer.scpt scripts/devonthink_importer.applescript
```

---

### Step 2: Create Migration Script (Python)

Create a new Python script `scripts/migrate_sha1_to_sha256.py` with progress tracking:

**Features**:
1. Scans the archive folder for PDF files
2. Tracks progress in a JSON file (`scripts/migration_progress.json`)
3. Skips files that have already been successfully migrated
4. Can be interrupted and resumed safely
5. Reports final statistics

**Usage**:
```bash
# Run migration (can be interrupted with Ctrl+C and resumed)
uv run python scripts/migrate_sha1_to_sha256.py

# Check progress without running
cat scripts/migration_progress.json | python -m json.tool
```

---

### Step 3: Execution Order

1. **First**: Update `devonthink_importer.applescript` and recompile
2. **Second**: Run migration script to update existing records
3. **Third**: Verify migration by spot-checking a few records in DEVONthink

---

## Files Modified

| File | Change |
|------|--------|
| `scripts/devonthink_importer.applescript` | Changed `shasum -a 1` to `shasum -a 256` |
| `scripts/devonthink_importer.scpt` | Recompiled from updated .applescript |
| `scripts/migrate_sha1_to_sha256.py` | **NEW** - Python migration script with progress tracking |

---

## Migration Results

- **Total files**: 107
- **Updated**: 107
- **Not found**: 0
- **Errors**: 0

---

## Notes

- SHA-1 produces 40-character hex strings
- SHA-256 produces 64-character hex strings
- Migration script (Python) kept in `scripts/` for potential future use
- Progress file (`migration_progress.json`) can be deleted after successful migration
- Migration is resumable: if interrupted, just run again and it will skip already-migrated files
