# Plan: SHA-1 Hash-Based Duplicate Detection for DEVONthink Importer

**Status:** Implemented
**Date:** 2025-12-26

## Summary

Add duplicate detection to the DEVONthink importer using SHA-1 content hashes. Before importing a file, check if an identical file (by hash) already exists anywhere in the target database. If found, replicate the existing record to the destination folder instead of importing again.

## Current Behavior

```
File arrives → Parse path → OCR import to destination → Trigger smart rules → Return "success"
```

**Problem:** Importing the same file twice creates duplicate records in DEVONthink.

## New Behavior

```
File arrives → Calculate SHA-1 → Search DB for matching hash
    ├─ Found: Replicate existing record to destination → Return "replicated"
    └─ Not found: OCR import as normal → Return "success"
```

**Key:** Python pipeline handles file deletion on success (unchanged).

## Implementation Approach

### AppleScript Changes (`scripts/devonthink_importer.applescript`)

**New flow in `on run argv`:**

```applescript
on run argv
    -- 1. Parse arguments and get destination group (existing code)

    -- 2. Calculate SHA-1 hash of incoming file
    set fileHash to do shell script "shasum -a 1 " & quoted form of filePath & " | awk '{print $1}'"

    -- 3. Search database for existing record with same content hash
    tell application id "DNtp"
        set existingRecords to search "contentHash==" & fileHash in theDatabase
    end tell

    -- 4. Branch: replicate vs import
    if (count of existingRecords) > 0 then
        -- Duplicate found: replicate to destination
        set existingRecord to item 1 of existingRecords
        tell application id "DNtp"
            replicate record existingRecord to destGroup
        end tell
        return "replicated"
    else
        -- No duplicate: OCR import as normal
        tell application id "DNtp"
            set theRecord to ocr file filePath to destGroup
            perform smart rule record theRecord trigger OCR event
        end tell
        return "success"
    end if
end run
```

### Key DEVONthink AppleScript Commands

| Command | Purpose |
|---------|---------|
| `search "contentHash==" & hash in database` | Find records by content hash |
| `content hash of record` | Get SHA-1 hash of existing record |
| `replicate record X to group` | Create replicant in destination |

### Return Values

| Return | Meaning | Python Action |
|--------|---------|---------------|
| `"success"` | New file imported via OCR | Delete source file |
| `"replicated"` | Existing record replicated | Delete source file |
| Error thrown | Something failed | Retry or notify |

## Files Modified

1. **`scripts/devonthink_importer.applescript`**
   - Added `calculateFileHash(filePath)` handler
   - Added `findRecordByHash(hash, database)` handler
   - Modified main `on run` to check for duplicates before import
   - Added replication logic for duplicates

2. **`scripts/devonthink_importer.scpt`** (recompiled)

## Edge Cases

| Case | Behavior |
|------|----------|
| Duplicate in same group | Still replicate (creates visible replicant) |
| Duplicate in different database | Import normally (hash search is per-database) |
| Multiple duplicates found | Use first match for replication |
| Hash calculation fails | Error, trigger retry |
