# Multi-Folder Watch Support

**Status:** Implemented

**Created:** 2025-12-27

## Overview

Add support for watching multiple folders, each with its own complete pipeline configuration.

## Requirements

- Each watch folder has its own pipeline (no sharing between folders)
- No backward compatibility needed - new config format only
- Menu bar shows aggregate and per-watcher stats

## New Config Structure

```json
{
  "watchers": [
    {
      "name": "RAP Research",
      "enabled": true,
      "watch": {
        "base_folder": "~/Documents/RAPPlatform-Import",
        "file_patterns": ["*.pdf"],
        "ignore_patterns": ["*.download", "*.crdownload", "*.tmp"],
        "stability_check_seconds": 1.0
      },
      "pipeline": {
        "retry_count": 3,
        "delete_on_success": true,
        "scripts": [...]
      }
    },
    {
      "name": "Quick Import",
      "enabled": true,
      "watch": { "base_folder": "~/Documents/QuickImport" },
      "pipeline": { "scripts": [...] }
    }
  ],
  "logging": { ... },
  "notifications": { ... }
}
```

## Implementation

### New Dataclasses

**WatcherConfig** (config.py):
```python
@dataclass
class WatcherConfig:
    name: str
    watch: WatchConfig
    pipeline: PipelineConfig
    enabled: bool = True
```

**WatcherInstance** (main.py):
```python
@dataclass
class WatcherInstance:
    name: str
    watcher: FileWatcher
    pipeline: PipelineManager
    config: WatcherConfig
```

### Modified Config

```python
@dataclass
class Config:
    watchers: list[WatcherConfig]
    logging: LoggingConfig
    notifications: NotificationsConfig

    @property
    def enabled_watchers(self) -> list[WatcherConfig]:
        return [w for w in self.watchers if w.enabled]
```

### Files Modified

| File | Changes |
|------|---------|
| `config.py` | Added WatcherConfig, modified Config to use watchers array |
| `main.py` | Added WatcherInstance, loop to create multiple watcher/pipeline pairs |
| `menubar.py` | Accept list of WatcherInstance, show aggregate + per-watcher counts |
| `config/config.json` | Converted to watchers array format |
| `tests/test_config.py` | Added 9 new tests for multi-watcher config |

## Architecture

### Shared Resources
- `ScriptExecutor` - shared across all watchers (stateless)
- `LoggingConfig` - global logging settings
- `NotificationsConfig` - global notification settings

### Isolated Per-Watcher
- `PipelineManager` - each watcher has its own with independent failure tracking
- `FileWatcher` - each watcher monitors its own folder
- File counts - tracked separately per watcher

### Log Output

```
INFO  RAP Importer starting
INFO  Enabled watchers: 2
INFO  Created watcher: RAP Research -> ~/Documents/RAPPlatform-Import
INFO  Created watcher: Quick Import -> ~/Documents/QuickImport
INFO  [RAP Research] Processing 3 existing files
INFO  [Quick Import] No files to process
INFO  [RAP Research] Started watching: ~/Documents/RAPPlatform-Import
INFO  [Quick Import] Started watching: ~/Documents/QuickImport
```

### Menu Bar Display

```
RAP Importer - 2 watchers
Files processed: 5
  RAP Research: 3
  Quick Import: 2
─────────────────────
Open Log File
Quit
```

## Testing

Added 9 new tests in `test_config.py`:
- `TestWatcherConfig` - 3 tests for WatcherConfig dataclass
- `TestConfigWithWatchers` - 2 tests for enabled_watchers filtering
- `TestLoadConfig` - 4 new tests for loading watchers array, validation errors

All 74 tests pass.
