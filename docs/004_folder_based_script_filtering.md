# Plan: Folder-Based Script Filtering

**Status:** Implemented

## Overview

Add `include_paths` and `exclude_paths` fields to `ScriptConfig` allowing scripts to run only on files matching specific folder patterns. Uses `fnmatch` glob patterns against `relative_path`.

## Design Decisions

| Decision | Choice |
|----------|--------|
| Field names | `include_paths` / `exclude_paths` |
| Match target | `relative_path` (e.g., `Liberty.University/BUSI770/Week01/file.pdf`) |
| Pattern syntax | `fnmatch` wildcards (`*`, `?`, `[seq]`) |
| No scripts match | Leave file in place (don't delete) |
| Exclude precedence | Exclude wins over include |
| Empty lists | Script runs on all files (backward compatible) |

## Filtering Logic

```
if exclude_paths matches → SKIP script
elif include_paths is empty → RUN script
elif include_paths matches → RUN script
else → SKIP script
```

## Example Configuration

```json
{
  "name": "Scholarly Assessment",
  "type": "command",
  "path": "uv run rap scholarly_assessment",
  "include_paths": ["*/BUSI*/*", "*/DISS*/*"],
  "exclude_paths": ["*/Archive/*", "*/Drafts/*"],
  "args": ["{file_path}"]
}
```

## Files Modified

### 1. `src/rap_importer_plugin/config.py`

Added fields to `ScriptConfig`:
```python
@dataclass
class ScriptConfig:
    # ... existing fields ...
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
```

Updated `_parse_script_config()`:
```python
include_paths=data.get("include_paths", []),
exclude_paths=data.get("exclude_paths", []),
```

### 2. `src/rap_importer_plugin/pipeline.py`

Added `fnmatch` import and new method:
```python
def _should_run_script(self, script: ScriptConfig, relative_path: str) -> bool:
    """Check if script should run based on path filters."""
    # No filters = run on all files
    if not script.include_paths and not script.exclude_paths:
        return True

    # Exclude takes precedence
    for pattern in script.exclude_paths:
        if fnmatch.fnmatch(relative_path, pattern):
            return False

    # No include patterns = run on all non-excluded
    if not script.include_paths:
        return True

    # Must match at least one include pattern
    return any(fnmatch.fnmatch(relative_path, p) for p in script.include_paths)
```

Updated `process_file()`:
```python
# Filter scripts by path
scripts = [
    s for s in self.config.enabled_scripts
    if self._should_run_script(s, variables.relative_path)
]

if not scripts:
    logger.info(f"No scripts matched path filters: {variables.relative_path}")
    return False  # Leave file in place
```

### 3. `tests/test_config.py`

Added tests:
- `test_include_paths_default_empty`
- `test_exclude_paths_default_empty`
- `test_include_paths_with_patterns`
- `test_exclude_paths_with_patterns`
- `test_include_and_exclude_paths_together`
- `test_load_config_with_path_filters`

### 4. `tests/test_pipeline.py` (new file)

Added tests:
- `test_no_filters_runs_all`
- `test_include_pattern_matches`
- `test_include_pattern_no_match`
- `test_multiple_include_patterns`
- `test_exclude_pattern_blocks`
- `test_multiple_exclude_patterns`
- `test_exclude_takes_precedence`
- `test_only_exclude_no_include`
- `test_database_level_pattern`
- `test_wildcard_patterns`
- `test_deep_nested_path`

### 5. `CLAUDE.md`

Updated "Adding Pipeline Scripts" section to document new fields and added "Path Filtering" subsection.

## Test Cases

| Scenario | include_paths | exclude_paths | relative_path | Result |
|----------|--------------|---------------|---------------|--------|
| No filters | `[]` | `[]` | any | RUN |
| Include match | `["BUSI*/*"]` | `[]` | `BUSI770/Week01/f.pdf` | RUN |
| Include no match | `["BUSI*/*"]` | `[]` | `DISS900/f.pdf` | SKIP |
| Exclude match | `[]` | `["*/Archive/*"]` | `BUSI770/Archive/f.pdf` | SKIP |
| Exclude precedence | `["BUSI*/*"]` | `["*/Draft/*"]` | `BUSI770/Draft/f.pdf` | SKIP |
| Database filter | `["Liberty*/*"]` | `[]` | `Liberty.University/x/f.pdf` | RUN |
