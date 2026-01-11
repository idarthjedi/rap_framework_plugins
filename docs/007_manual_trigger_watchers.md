# Plan: Manual Trigger Watchers

**Status**: Implemented

## Problem Statement
Current watchers auto-trigger on file changes (watchdog) or startup. Need a new watcher type that only triggers when a menu item is clicked - specifically for `obsidian-sync` which should run on-demand, not automatically.

## User Requirements (Confirmed)
1. **Trigger field**: Add `trigger: "auto" | "manual"` to config (default: "auto")
2. **Archive option**: Configurable per-watcher, defaults based on trigger type
3. **Menu item**: Manual watchers appear in "Run Manual" submenu
4. **File context**: Manual pipelines get `{base_folder}` only, NOT individual file paths
5. **No watchdog**: Manual watchers don't use file watching
6. **No startup processing**: Manual watchers don't process on startup

---

## Architecture Overview

```
┌─ Auto Watchers (Current) ─────────────────────────────────┐
│ Config → FileWatcher (watchdog) → on_file_ready callback │
│                                  → pipeline.process_file  │
│ Also triggers on startup scan                              │
└───────────────────────────────────────────────────────────┘

┌─ Manual Watchers (New) ───────────────────────────────────┐
│ Config → Menu Item ("Run Manual" submenu)                 │
│                     → pipeline.run_manual()               │
│ No watchdog, no startup processing                        │
│ Uses ManualVariables ({base_folder}, {log_level} only)   │
└───────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Configuration Changes

**Files**: `config.py`, `config.schema.json`

Add to `WatcherConfig`:
```python
trigger: str = "auto"  # "auto" or "manual"
archive: bool | None = None  # None = default based on trigger

@property
def should_archive(self) -> bool:
    if self.archive is not None:
        return self.archive
    return self.trigger == "auto"  # Auto archives, manual doesn't
```

### Phase 2: Variable Substitution

**File**: `executor.py`

Add `ManualVariables` class for non-file variable substitution:
```python
@dataclass
class ManualVariables:
    base_folder: str
    log_level: str = "INFO"

    @classmethod
    def from_watch_config(cls, watch_config, log_level="INFO"):
        return cls(
            base_folder=str(watch_config.expanded_base_folder),
            log_level=log_level,
        )
```

### Phase 3: Pipeline Manager

**File**: `pipeline.py`

Add `run_manual()` method:
- Creates `ManualVariables` from watch config
- Executes all enabled scripts once (no path filtering)
- No archiving (controlled by config)
- Uses same logging/notification infrastructure

Add `archive` parameter to `__init__()`:
- Pass from `WatcherConfig.should_archive`
- Conditionally call `_archive_file()` in `_do_process_file()`

### Phase 4: WatcherInstance Updates

**File**: `main.py`

Make `watcher` optional in `WatcherInstance`:
```python
@dataclass
class WatcherInstance:
    name: str
    watcher: FileWatcher | None  # None for manual watchers
    pipeline: PipelineManager
    config: WatcherConfig

    @property
    def is_manual(self) -> bool:
        return self.config.trigger == "manual"
```

Update watcher creation:
- Only create `FileWatcher` for `trigger == "auto"`
- Skip manual watchers in startup file processing
- Handle `None` watcher in signal handlers

### Phase 5: Menu Bar

**File**: `menubar.py`

Add "Run Manual" submenu:
```
RAP Importer - 2 watchers
Files processed: 150
  RAP Research: 143
  Obsidian Transformer: 7
---
Run Manual ▶
  Obsidian Sync     <-- Clicking runs pipeline.run_manual()
---
Retry - 0
Open Log File
Quit
```

Implementation:
- Separate watchers into `auto_watchers` and `manual_watchers`
- Create submenu with callback for each manual watcher
- Add `_manual_pending` counter for menu bar display
- Run in background thread (same pattern as Retry)

---

## Files Modified

| File | Changes |
|------|---------|
| `config.py` | Add `trigger`, `archive`, `should_archive`, `is_manual` to WatcherConfig |
| `config.schema.json` | Add schema for new fields |
| `executor.py` | Add `ManualVariables` class |
| `pipeline.py` | Add `run_manual()`, `archive` param, conditional archiving |
| `main.py` | Optional `watcher`, skip manual in startup, signal handling |
| `menubar.py` | "Run Manual" submenu, click callbacks, pending counter |

---

## Example Configuration

```json
{
  "name": "Obsidian Sync",
  "enabled": true,
  "trigger": "manual",
  "archive": false,
  "watch": {
    "base_folder": "~/RAP/ObsidianSync"
  },
  "pipeline": {
    "scripts": [
      {
        "name": "Sync to Obsidian",
        "type": "command",
        "path": "uv run obsidian-sync",
        "cwd": "~/development/anthropics/projects/rap_obsidian_utils",
        "args": [
          "--source", "{base_folder}",
          "--dest", "~/Obsidian/Leadership/Slip-Box"
        ]
      }
    ]
  }
}
```

**Key points:**
- `{base_folder}` → source path (the "watched" folder)
- Destination path is configured directly in args
- The importer just runs the command; `obsidian-sync` handles all file operations

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Reuse `PipelineManager` | Add `run_manual()` method | Shares executor, logging, notifications |
| `WatcherInstance.watcher` | Make Optional | Simpler than dummy/null watcher class |
| Variable substitution | New `ManualVariables` class | Clean separation, prevents invalid access |
| Archive default | Based on trigger type | Manual runs typically don't produce files |
| Menu structure | "Run Manual" submenu | Groups manual watchers cleanly |

---

## Verification Plan

1. Add manual watcher to config
2. Start importer, verify menu shows "Run Manual" submenu
3. Verify auto watchers still process on file changes
4. Click manual watcher menu item, verify pipeline runs
5. Verify no archiving occurs (when `archive: false`)
6. Run all tests to ensure no regressions
