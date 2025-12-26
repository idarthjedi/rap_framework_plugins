# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RAP Importer is a Python-based file watcher with a configurable pipeline for automatically importing PDFs into DEVONthink with OCR. Files are routed to the correct database and group based on their folder structure.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Python File Watcher                          │
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
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

| Module | Purpose |
|--------|---------|
| `cli.py` | Command-line argument parsing (argparse) |
| `config.py` | Config schema (dataclasses) + loader |
| `watcher.py` | File watching with stability checks (watchdog) |
| `executor.py` | Script execution (osascript, subprocess) |
| `pipeline.py` | Pipeline orchestration with retry logic |
| `menubar.py` | macOS menu bar app (rumps) |
| `notifications.py` | macOS notifications (osascript) |
| `logging_config.py` | Logging with TRACE level, rotation |

### Execution Modes

- `--background` (default): Continuous watching with macOS menu bar icon
- `--runonce`: Process existing files and exit

## Development

### Commands

```bash
# Install dependencies
uv sync

# Run the app
uv run rap-importer

# Run with debug logging
uv run rap-importer --log-level DEBUG

# Run tests
uv run python -m pytest tests/ -v

# Compile AppleScript after changes
osacompile -o scripts/devonthink_importer.scpt scripts/devonthink_importer.applescript
```

### Project Structure

```
rap_importer/
├── config/
│   └── config.json             # Runtime configuration
├── main.py                     # CLI entry point
├── src/rap_importer/          # Python package
│   ├── cli.py                 # Argument parsing
│   ├── config.py              # Config schema + loader
│   ├── executor.py            # Script execution
│   ├── logging_config.py      # Logging setup
│   ├── main.py                # Main app logic
│   ├── menubar.py             # macOS menu bar
│   ├── notifications.py       # macOS notifications
│   ├── pipeline.py            # Pipeline management
│   └── watcher.py             # File watching
├── scripts/
│   ├── devonthink_importer.applescript  # Source
│   └── devonthink_importer.scpt         # Compiled
├── docs/                       # Historical plan documents (atomic, read-only)
└── tests/                      # Test suite
```

### Adding Pipeline Scripts

Add to `config/config.json`:

```json
{
  "name": "My Script",
  "type": "python",
  "path": "scripts/my_script.py",
  "enabled": true,
  "args": ["--file", "{file_path}", "--db", "{database}"]
}
```

Variable substitution in args:
- `{file_path}` - Full POSIX path
- `{relative_path}` - Path from watch folder
- `{filename}` - Just filename
- `{database}` - First path component
- `{group_path}` - Path between database and filename

## DEVONthink AppleScript Reference

View the scripting dictionary:
```bash
sdef /Applications/DEVONthink\ 3.app | less
```

### Common Commands

```applescript
tell application id "DNtp"
    -- Get database by name
    set theDatabase to database "DatabaseName"

    -- Get incoming group (inbox)
    set inbox to incoming group of theDatabase

    -- Create nested groups
    set destGroup to create location "Group/SubGroup" in theDatabase

    -- OCR and import
    set theRecord to ocr file "/path/to/file.pdf" to destGroup

    -- Trigger smart rules
    perform smart rule record theRecord trigger OCR event
end tell
```

### Simplified AppleScript

The `scripts/devonthink_importer.applescript` is a simplified, argument-driven script:

```applescript
on run argv
    set filePath to item 1 of argv
    set relativePath to item 2 of argv
    -- Parse path, get database/group, OCR import, trigger rules
    return "success"
end run
```

Called via: `osascript devonthink_importer.scpt "/full/path" "Database/Group/file.pdf"`

## Key Dependencies

| Package | Purpose |
|---------|---------|
| watchdog | File system event monitoring |
| rumps | macOS menu bar apps |
| DEVONthink Pro | Document management (bundle ID: `DNtp`) |

## Documentation Conventions

### Approved Plans

When a plan is approved and ready for implementation, save it to the `docs/` folder using this naming convention:

```
docs/<NNN>_<plan_name>.md
```

Where:
- `<NNN>` is a three-digit sequential number (000, 001, 002, ...)
- `<plan_name>` is a snake_case description of the plan

**Examples:**
- `docs/000_python_file_watcher_pipeline.md`
- `docs/001_multi_database_support.md`
- `docs/002_custom_ocr_settings.md`

To determine the next number, check existing files in `docs/` and increment from the highest.

**Important:** Plans in `docs/` are **atomic point-in-time references**. Once created, they should NOT be updated. When a plan is complete, mark it as "Implemented" in the plan's status field. These documents serve as historical records for look-back purposes.
