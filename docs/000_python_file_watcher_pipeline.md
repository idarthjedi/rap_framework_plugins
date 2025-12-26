# Plan: Python File Watcher with Configurable Script Pipeline

**Status:** Implemented
**Date:** 2024-12-26

## Summary

Refactor the architecture to move file monitoring from AppleScript to Python, creating a configurable pipeline system that can run multiple scripts (AppleScript or Python) in sequence for each detected file.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Python File Watcher                          │
│                        (main.py)                                  │
│                                                                   │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────────┐   │
│  │  Watchdog   │───►│   Pipeline   │───►│  Script Executor  │   │
│  │  Observer   │    │   Manager    │    │                   │   │
│  └─────────────┘    └──────────────┘    └───────────────────┘   │
│        │                   │                     │               │
│        ▼                   ▼                     ▼               │
│  ┌─────────────┐    ┌──────────────┐    ┌───────────────────┐   │
│  │ File Events │    │ config.json  │    │ AppleScript/      │   │
│  │ (*.pdf etc) │    │              │    │ Python scripts    │   │
│  └─────────────┘    └──────────────┘    └───────────────────┘   │
│                                                   │               │
│                                                   ▼               │
│                                          ┌───────────────────┐   │
│                                          │ devonthink_       │   │
│                                          │ importer.scpt     │   │
│                                          │ (OCR only)        │   │
│                                          └───────────────────┘   │
│                                                                   │
│  On success: Delete original file                                 │
│  On failure: Retry up to N times, then ignore until restart       │
│  Notifications: macOS Notification Center for errors              │
└─────────────────────────────────────────────────────────────────┘
```

## Execution Modes

The application supports two execution modes via command-line flags:

### `--runonce` Mode
- Scans the configured watch folder for all existing files matching the file patterns
- Processes each file through the pipeline sequentially
- Exits when all files have been processed (or failed max retries)
- Useful for: initial processing, cron jobs, manual batch imports

### `--background` Mode
- Starts continuous file watching using watchdog
- Processes existing files first, then monitors for new files
- Shows a **menu bar icon** (macOS) so user knows it's running
- Menu bar provides: status info, manual quit option
- Stays running until explicitly quit

### Menu Bar App (background mode)

When running in background mode, the app appears in the macOS menu bar with:
- **Icon**: Small indicator showing the app is running
- **Menu items**:
  - "RAP Importer - Running" (status)
  - "Files processed: N" (counter)
  - "Open Log File" (opens log in Console.app)
  - "Quit" (graceful shutdown)

Uses the `rumps` library for simple macOS menu bar integration.

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Config format | JSON | No dependencies, universal |
| Pipeline errors | Stop on first failure | Simpler, safer for file operations |
| File patterns | Configurable | Flexibility for future use cases |
| File watching | `watchdog` library | Robust, cross-platform, event-driven |
| Logging | Python `logging` module | DEBUG/TRACE levels, file + console |
| Notifications | `osascript` for macOS | No extra dependencies |
| Menu bar | `rumps` library | Simple macOS menu bar apps |
| Execution modes | CLI flags | Flexible for different use cases |

## Project Structure

```
rap_importer/
├── pyproject.toml              # Project config, dependencies
├── config.json                 # Pipeline configuration
├── main.py                     # Entry point
├── src/rap_importer/          # Python package
│   ├── cli.py                 # CLI argument parsing
│   ├── config.py              # Config loading/validation
│   ├── executor.py            # Script execution
│   ├── logging_config.py      # Logging setup
│   ├── main.py                # Main app logic
│   ├── menubar.py             # macOS menu bar app
│   ├── notifications.py       # macOS notifications
│   ├── pipeline.py            # Pipeline execution
│   └── watcher.py             # File watching logic
├── scripts/
│   ├── devonthink_importer.applescript  # Simplified AppleScript
│   └── devonthink_importer.scpt         # Compiled version
└── tests/                      # Test suite
```

## Implementation Phases

### Phase 1: Core Infrastructure ✓
- Set up project structure with `uv`
- Create config schema and loader
- Implement logging configuration
- Implement notifications module
- Implement CLI argument parsing

### Phase 2: File Watching ✓
- Implement FileWatcher with watchdog
- Implement stability checking
- Add file pattern matching
- Add `scan_existing_files()` function

### Phase 3: Pipeline Execution ✓
- Implement ScriptExecutor (AppleScript + Python)
- Implement PipelineManager with retry logic
- Add variable substitution

### Phase 4: Execution Modes ✓
- Implement `--runonce` mode
- Implement `--background` mode with menu bar
- Implement menu bar app with rumps
- Add graceful shutdown handling

### Phase 5: AppleScript Refactoring ✓
- Simplify devonthink_importer.applescript
- Remove monitoring, add argument handling
- Remove deletion logic

### Phase 6: Testing ✓
- Write unit tests (40 tests passing)

### Phase 7: Documentation ✓
- Update README.md
- Update CLAUDE.md
