# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RAP Importer is a Python-based file watcher with a configurable pipeline for automatically importing PDFs into DEVONthink with OCR. Files are routed to the correct database and group based on their folder structure.

## Documentation

### Wiki (User-Facing Documentation)

The RAP Framework Wiki contains user-facing documentation for this project:
- **Location:** `~/development/anthropics/projects/rap_framework.wiki/`
- **URL:** https://github.com/idarthjedi/rap_framework/wiki

**When to update the wiki:**
- Adding or changing CLI options
- Modifying pipeline configuration format
- Changing watch folder behavior
- Adding new script types or variables
- Updating installation or setup procedures

**Wiki pages for this project:**

| Change Type | Wiki Page(s) to Update |
|-------------|------------------------|
| CLI options | `CLI-Reference.md` |
| Installation/setup | `Installation.md`, `Importer-Setup.md` |
| Pipeline config | `Pipeline-Configuration.md`, `Configuration.md` |
| DEVONthink integration | `DEVONthink-Integration.md` |
| Architecture changes | `System-Overview.md`, `Data-Flow.md` |

### Documentation Map

| Topic | Location | Audience |
|-------|----------|----------|
| User guide, CLI reference | [RAP Framework Wiki](https://github.com/idarthjedi/rap_framework/wiki) | Users |
| Implementation details | This file (`CLAUDE.md`) | Developers |
| Config schema | `config/config.schema.json` | Developers |

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

- `--background` (default): Spawn daemon in background, return to terminal
- `--foreground`: Run in terminal with console output (for debugging)
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
rap_importer_plugin/
├── config/
│   └── config.json             # Runtime configuration
├── main.py                     # CLI entry point
├── src/rap_importer_plugin/   # Python package
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
  "reqs": "Description of requirements/dependencies for this script",
  "type": "python",
  "path": "scripts/my_script.py",
  "enabled": true,
  "args": ["--file", "{file_path}", "--db", "{database}"]
}
```

Script configuration fields:
- `name` - Display name for the script
- `reqs` - (Optional) Requirements/dependencies description (e.g., "DEVONthink 4 must be running")
- `type` - Script type: `"applescript"`, `"python"`, or `"command"`
- `path` - Script path (relative to project) or command string
- `enabled` - Whether to run this script
- `args` - Arguments with variable substitution
- `cwd` - (Optional) Working directory for command type
- `include_paths` - (Optional) Run only on files matching these patterns
- `exclude_paths` - (Optional) Skip files matching these patterns (takes precedence)

Variable substitution in args:
- `{file_path}` - Full POSIX path to the file
- `{relative_path}` - Path from watch folder
- `{filename}` - Just the filename
- `{database}` - First path component (for DEVONthink routing)
- `{group_path}` - Path between database and filename
- `{base_folder}` - The watch folder base path
- `{log_level}` - Current log level (DEBUG, INFO, etc.)

### Path Filtering

Scripts can be configured to run only on specific folders using `include_paths` and `exclude_paths`. Patterns use `fnmatch` glob syntax and match against `relative_path` (e.g., `Database/Group/file.pdf`).

**Filtering logic (order of precedence):**
1. No filters (both empty) → script runs on all files
2. **Exclude patterns checked first** → if any match, script is skipped (blocklist)
3. No include patterns → script runs on all non-excluded files
4. **Include patterns checked second** → at least one must match (allowlist)

**Key principle:** Exclude always wins over include. This "deny-first" approach prevents accidental inclusion of paths that match both patterns.

**Example 1:** Include a database except specific subfolders
```json
{
  "include_paths": ["Liberty University/*"],
  "exclude_paths": ["Liberty University/Harvard Business Review/*"]
}
```
→ Runs on all Liberty University files EXCEPT Harvard Business Review

**Example 2:** Only run on specific subfolder, ignore rest
```json
{
  "include_paths": ["Liberty University/Harvard Business Review/*"]
}
```
→ Runs ONLY on Harvard Business Review files (no exclude needed)

**Example 3:** Run on everything except Archive folders
```json
{
  "exclude_paths": ["*/Archive/*"]
}
```
→ Runs on all files EXCEPT those in Archive folders

Common patterns:
- `*/BUSI*/*` - Match BUSI courses in any database
- `Liberty*/*` - Match Liberty database (with wildcard for variations)
- `*/Archive/*` - Match Archive folder at any depth
- `*/Week0?/*` - Match Week01-Week09 using `?` single-character wildcard

### Global Exclude Paths

At the watcher level, `global_exclude_paths` can exclude folders from ALL processing:

```json
{
  "name": "RAP Research",
  "global_exclude_paths": ["*/EndNote/*", "*/Staging/*"],
  "watch": { ... },
  "pipeline": { ... }
}
```

Files matching these patterns:
- Are silently skipped (DEBUG log only)
- Never trigger any scripts
- Are never deleted (even if `delete_on_success=true`)

**Use case:** Staging folders where files are copied for other processes to handle via their own pipelines.

### Config Schema

The config structure is defined in `config/config.schema.json` (JSON Schema Draft 7). This enables:
- IDE autocomplete when editing `config.json`
- Schema validation in tests (`tests/test_schema.py`)

**IMPORTANT:** When modifying the config structure:
1. Update `src/rap_importer_plugin/config.py` (dataclasses)
2. Update `config/config.schema.json` (JSON Schema)
3. Run `uv run python -m pytest tests/test_schema.py -v` to verify

The schema tests validate:
- Schema is valid JSON Schema Draft 7
- `config/config.json` validates against the schema
- Invalid configs are correctly rejected

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
