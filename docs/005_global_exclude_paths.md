# Plan: Global Exclude Paths at Watcher Level

**Status:** Implemented

## Overview

Add `global_exclude_paths` field to `WatcherConfig` allowing folders to be globally excluded from all scripts and deletion. Files matching these patterns are silently skipped (DEBUG log only).

**Use case:** Staging folders where the importer copies files for other processes to handle via their own pipelines.

## Design Decisions

| Decision | Choice |
|----------|--------|
| Field location | `WatcherConfig` (top-level on watcher object) |
| Field name | `global_exclude_paths` |
| Match target | `relative_path` (e.g., `Liberty.University/EndNote/file.pdf`) |
| Pattern syntax | `fnmatch` wildcards (`*`, `?`, `[seq]`) |
| Logging | DEBUG level (silent skip) |
| Effects | Skips ALL scripts AND prevents deletion |

## Filtering Logic

```
process_file() called with file_path
    ↓
Compute relative_path from base_folder
    ↓
Check global_exclude_paths patterns ←── NEW (early exit)
    if any pattern matches → return False (skip file entirely)
    ↓
Check per-script include/exclude patterns (existing)
    ↓
Execute matching scripts
    ↓
Delete file if delete_on_success=true AND not globally excluded
```

## Example Configuration

```json
{
  "watchers": [
    {
      "name": "RAP Research",
      "enabled": true,
      "global_exclude_paths": [
        "*/EndNote/*",
        "*/Staging/*"
      ],
      "watch": {
        "base_folder": "~/Documents/RAPPlatform-Import"
      },
      "pipeline": {
        "scripts": [...]
      }
    }
  ]
}
```

## Files Modified

### 1. `src/rap_importer_plugin/config.py`

Added `global_exclude_paths` field to `WatcherConfig` dataclass and updated `_parse_watcher_config()`.

### 2. `src/rap_importer_plugin/pipeline.py`

- Added `global_exclude_paths` parameter to `PipelineManager.__init__()`
- Added `_is_globally_excluded()` method
- Updated `process_file()` to check global excludes early (before any processing)

### 3. `src/rap_importer_plugin/main.py`

Updated pipeline creation to pass `global_exclude_paths` from watcher config.

### 4. `config/config.schema.json`

Added `global_exclude_paths` field to WatcherConfig definition.

### 5. `tests/test_config.py`

Added tests:
- `test_global_exclude_paths_default_empty`
- `test_global_exclude_paths_with_patterns`
- `test_load_config_with_global_excludes`

### 6. `tests/test_pipeline.py`

Added tests:
- `test_no_global_excludes_allows_all`
- `test_global_exclude_pattern_matches`
- `test_global_exclude_pattern_no_match`
- `test_global_exclude_multiple_patterns`
- `test_global_exclude_with_database_pattern`

### 7. `CLAUDE.md`

Added "Global Exclude Paths" section under Path Filtering.

## Test Cases

| Scenario | global_exclude_paths | relative_path | Result |
|----------|---------------------|---------------|--------|
| No global excludes | `[]` | any | PROCESS |
| Pattern matches | `["*/EndNote/*"]` | `Liberty/EndNote/file.pdf` | SKIP |
| Pattern no match | `["*/EndNote/*"]` | `Liberty/BUSI770/file.pdf` | PROCESS |
| Multiple patterns | `["*/EndNote/*", "*/Staging/*"]` | `Liberty/Staging/file.pdf` | SKIP |
| Nested match | `["*/EndNote/*"]` | `Liberty/EndNote/Sub/file.pdf` | SKIP |
