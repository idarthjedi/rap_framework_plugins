  # RAP Importer Plugin

A Python-based file watcher with configurable pipeline for automatically importing PDFs into DEVONthink with OCR. Files are routed to the correct database and group based on their folder structure.

## Overview

Drop PDF files into a watched folder structure that mirrors your DEVONthink databases:

```
~/Documents/RAPPlatform-Import/
├── Liberty.University/           → Database: "Liberty.University"
│   ├── Inbox/                    → database's incoming group
│   │   └── lecture.pdf
│   ├── BUSI770/                  → group "BUSI770" (created if needed)
│   │   └── Week 01/              → group "BUSI770/Week 01" (created if needed)
│   │       └── assignment.pdf
│   └── quick-note.pdf            → incoming group (root-level files)
```

## Features

- **Python-based file watching** using watchdog for reliable, event-driven monitoring
- **Configurable pipeline** with support for AppleScript and Python scripts
- **Duplicate detection**: SHA-1 hash-based detection prevents importing the same file twice
- **Automatic archiving**: Processed files are moved to `_Archived/` folder (not deleted)
- **Three execution modes**:
  - `--background` (default): Spawn daemon, return to terminal
  - `--foreground`: Run in terminal with console output (for debugging)
  - `--runonce`: Process existing files and exit (great for cron jobs)
- **Menu bar app**: Shows running status, file count, and easy quit access
- **Retry logic**: Failed files are retried up to N times with configurable delays
- **macOS notifications**: Get notified of errors (and optionally successes)
- **Flexible logging**: DEBUG/TRACE levels with file rotation
- **Path filter simulation**: Test how files would be processed without running the pipeline

## Prerequisites

> **Note:** This tool is macOS-only. It uses AppleScript, macOS menu bar integration, and DEVONthink (a macOS application).

- **macOS** (tested on macOS Sonoma)
- **Python 3.12+**
- **DEVONthink Pro** installed
- **uv** package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))

## Installation

### 1. Clone the Repository

```bash
git clone <repo-url>
cd rap_importer_plugin
```

### 2. Install Dependencies

```bash
uv sync
```

### 3. Create the Watch Folder

```bash
mkdir -p ~/Documents/RAPPlatform-Import/
```

### 4. Create Database Subfolders

Create a subfolder for each DEVONthink database you want to use:

```bash
# Example for a database named "Liberty.University"
mkdir -p ~/Documents/RAPPlatform-Import/Liberty.University/Inbox
```

### 5. Configure (Optional)

Edit `config/config.json` to customize settings. See [Configuration](#configuration) below.

## Usage

### Run in Background Mode (default)

```bash
uv run rap-importer
```

Or with the `--background` flag explicitly:

```bash
uv run rap-importer --background
```

This will:
1. Spawn a background daemon process
2. Return control to your terminal immediately
3. Show a menu bar icon ("RAP") with status and quit option
4. Process existing files and watch for new ones
5. Log all activity to `~/Library/Logs/rap-importer.log`

### Run in Foreground Mode (for debugging)

```bash
uv run rap-importer --foreground
```

This will:
1. Run directly in your terminal (blocks until quit)
2. Show log output in the console with colors
3. Show a menu bar icon ("RAP") with status and quit option
4. Process existing files and watch for new ones

Use this mode when testing or debugging the watcher.

### Run Once Mode

Process all existing files and exit:

```bash
uv run rap-importer --runonce
```

### Simulate Mode

Test how files would be processed by your pipeline configuration without actually running any scripts:

```bash
uv run rap-importer --simulate
```

This displays a table showing:
- Which paths are globally excluded
- Which scripts would run for each path
- Why scripts are skipped (no include match vs. excluded by pattern)

You can also test specific paths:

```bash
uv run rap-importer --simulate "Liberty University/BUSI770/assignment.pdf"
```

Example output:

```
╭──────────────────────────────╮
│ Watcher: RAP Research        │
│ Global Excludes: */EndNote/* │
╰──────────────────────────────╯
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Test Path                     ┃ Global ┃ Script A ┃ Script B  ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━┩
│ Liberty University/file.pdf   │   ✓    │  ✓ RUN   │  ✓ RUN    │
│ Liberty University/EndNote/…  │ ✗ EXCL │    -     │    -      │
│ Other Database/file.pdf       │   ✓    │  ✗ skip  │  ✗ skip   │
└───────────────────────────────┴────────┴──────────┴───────────┘

Legend: ✓ RUN = script executes, ✗ excl = excluded, ✗ skip = no include match
```

### Command-Line Options

```
rap-importer [-h] [--background | --foreground | --runonce | --simulate]
             [--config FILE] [--log-level LEVEL] [--version] [TEST_PATHS...]

Options:
  -h, --help            Show help message
  --background          Run in background, return control to terminal (default)
  --foreground          Run in foreground with console output (for debugging)
  --runonce             Process existing files and exit
  --simulate            Display path filtering simulation table
  --config, -c FILE     Path to config file (default: config/config.json)
  --log-level, -l LEVEL Override log level (TRACE, DEBUG, INFO, WARNING, ERROR)
  --version, -v         Show version

Arguments:
  TEST_PATHS            Optional paths to test (used with --simulate)
```

### Stopping the Background Daemon

Use one of these methods to stop the running daemon:
- Click "Quit" in the menu bar app
- Find and kill the process: `pkill -f "rap_importer.main"`

## Configuration

Configuration is stored in `config/config.json`. The config supports multiple watchers, each with their own watch folder and pipeline:

```json
{
  "watchers": [
    {
      "name": "RAP Research",
      "enabled": true,
      "global_exclude_paths": ["*/EndNote/*", "*/Staging/*"],
      "watch": {
        "base_folder": "~/Documents/RAPPlatform-Import",
        "file_patterns": ["*.pdf"],
        "ignore_patterns": ["*.download", "*.crdownload", "*.tmp"],
        "stability_check_seconds": 1.0,
        "stability_timeout_seconds": 60
      },
      "pipeline": {
        "retry_count": 3,
        "retry_delay_seconds": 5,
        "delete_on_success": true,
        "scripts": [
          {
            "name": "DEVONthink Import",
            "type": "applescript",
            "path": "scripts/devonthink_importer.scpt",
            "enabled": true,
            "args": ["{file_path}", "{relative_path}"]
          }
        ]
      }
    }
  ],
  "logging": {
    "level": "INFO",
    "file": "~/Library/Logs/rap-importer.log",
    "max_bytes": 10485760,
    "backup_count": 5
  },
  "notifications": {
    "enabled": true,
    "on_error": true,
    "on_success": false
  }
}
```

### Configuration Options

#### Watcher Options

| Option | Default | Description |
|--------|---------|-------------|
| name | (required) | Display name for this watcher |
| enabled | true | Whether this watcher is active |
| global_exclude_paths | [] | Patterns to skip globally (all scripts and deletion) |

#### Watch Options

| Option | Default | Description |
|--------|---------|-------------|
| base_folder | (required) | Folder to watch |
| file_patterns | ["*.pdf"] | File patterns to process |
| ignore_patterns | ["*.download", ...] | Patterns to ignore |
| stability_check_seconds | 1.0 | Delay between size checks |
| stability_timeout_seconds | 60 | Max time to wait for stable file |

#### Pipeline Options

| Option | Default | Description |
|--------|---------|-------------|
| retry_count | 3 | Max retries for failed files |
| retry_delay_seconds | 5 | Delay between retries |

**Note:** Successfully processed files are automatically archived to `{base_folder}/_Archived/` preserving folder structure. The `_Archived` folder is automatically excluded from processing.

#### Logging Options

| Option | Default | Description |
|--------|---------|-------------|
| level | INFO | Log level (TRACE/DEBUG/INFO/WARNING/ERROR) |
| file | ~/Library/Logs/rap-importer.log | Log file path |

#### Notification Options

| Option | Default | Description |
|--------|---------|-------------|
| enabled | true | Enable macOS notifications |
| on_error | true | Notify on errors |
| on_success | false | Notify on success |

### Script Types

The pipeline supports three script types:

| Type | Description | Example |
|------|-------------|---------|
| `applescript` | Runs AppleScript via `osascript` | Built-in DEVONthink importer |
| `python` | Runs Python script via current interpreter | Custom Python scripts |
| `command` | Runs arbitrary shell command | `uv run`, `npm run`, etc. |

#### Command Type Example

The `command` type is useful for running CLI tools in different project directories:

```json
{
  "name": "Scholarly Assessment",
  "type": "command",
  "path": "uv run rap scholarly_assessment",
  "enabled": true,
  "cwd": "~/projects/research_analysis_platform",
  "args": ["{filename}", "--headless", "--output-dir=~/Obsidian/Slip-Box/{group_path}/"]
}
```

The `cwd` field specifies the working directory for command execution. Both `cwd` and `args` support `~` expansion and variable substitution.

### Script Variable Substitution

Scripts can use these variables in their `path` (for command type) and `args`. Variables are **watcher-specific** - each watcher uses its own `base_folder` from its configuration.

| Variable | Description | Example Value |
|----------|-------------|---------------|
| `{file_path}` | Full POSIX path to the file | `/Users/you/Documents/RAPPlatform-Import/Liberty.University/BUSI770/file.pdf` |
| `{relative_path}` | Path relative to watch folder | `Liberty.University/BUSI770/file.pdf` |
| `{filename}` | Just the filename | `file.pdf` |
| `{database}` | First path component (database name) | `Liberty.University` |
| `{group_path}` | Path between database and filename | `BUSI770` |
| `{base_folder}` | Watch folder base path | `/Users/you/Documents/RAPPlatform-Import` |
| `{log_level}` | Current log level | `INFO` |

### Script Configuration Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Human-readable script name |
| `reqs` | string | No | Requirements/dependencies description |
| `type` | string | Yes | `applescript`, `python`, or `command` |
| `path` | string | Yes | Script path or command string |
| `enabled` | boolean | No | Whether to run (default: true) |
| `args` | list/dict | No | Additional arguments |
| `cwd` | string | No | Working directory (for command type) |
| `include_paths` | list | No | Run only on files matching these patterns |
| `exclude_paths` | list | No | Skip files matching these patterns (takes precedence) |

### Path Filtering

Scripts can be configured to run only on specific folders using `include_paths` and `exclude_paths`. Patterns use `fnmatch` glob syntax and match against `relative_path`.

**Filtering logic:**
1. No filters (both empty) → script runs on all files
2. **Exclude patterns checked first** → if any match, script is skipped
3. **Include patterns checked second** → at least one must match

**Example:** Run only on BUSI courses, excluding Archive folders:
```json
{
  "name": "Scholarly Assessment",
  "type": "command",
  "path": "uv run rap scholarly_assessment",
  "include_paths": ["*/BUSI*/*"],
  "exclude_paths": ["*/Archive/*"]
}
```

### Global Exclude Paths

At the watcher level, `global_exclude_paths` excludes folders from ALL processing:

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

## Folder Structure Reference

| File Location | DEVONthink Destination |
|--------------|------------------------|
| `DatabaseName/file.pdf` | Database's incoming group |
| `DatabaseName/Inbox/file.pdf` | Database's incoming group |
| `DatabaseName/Inbox/SubGroup/file.pdf` | Subgroup within incoming group |
| `DatabaseName/GroupA/file.pdf` | Group "GroupA" (created if needed) |
| `DatabaseName/GroupA/GroupB/file.pdf` | Group "GroupA/GroupB" (created if needed) |

### Special Behaviors

- **Database must exist**: If the database doesn't exist in DEVONthink, the file fails with error
- **Groups auto-created**: Non-Inbox groups are created automatically
- **Smart rules triggered**: After OCR, smart rules with "OCR event" trigger are executed
- **Original archived**: Successfully imported files are moved to `_Archived/` folder (preserving path structure)

## Duplicate Detection

The importer uses SHA-1 hash-based duplicate detection to prevent importing the same file multiple times.

### How It Works

1. **Before import**: Calculate SHA-1 hash of the incoming file
2. **Search database**: Look for existing records with matching `sourceHash` custom metadata
3. **If duplicate found**: Replicate the existing record to the destination folder (no new file created)
4. **If no duplicate**: Import via OCR and store the hash as `sourceHash` metadata

### Why Custom Metadata?

DEVONthink's built-in `contentHash` property changes after OCR processing (the PDF is modified during OCR). To detect true duplicates, we store the original file's SHA-1 hash as custom metadata before any processing occurs.

### Behavior Summary

| Scenario | Result | Return Value |
|----------|--------|--------------|
| New file | OCR import, store hash | `"success"` |
| Duplicate, different folder | Replicate to destination | `"replicated"` |
| Duplicate, same folder | No action needed | `"replicated"` |
| Duplicate, different database | OCR import (search is per-database) | `"success"` |

### Viewing Source Hash

To see the stored hash for a record in DEVONthink:
1. Select the record
2. Open the Info inspector (⌘I)
3. Look for "sourceHash" in Custom metadata

## Troubleshooting

### Log File Location

```bash
cat ~/Library/Logs/rap-importer.log
```

Or follow in real-time:

```bash
tail -f ~/Library/Logs/rap-importer.log
```

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| "Database not found" | Database name doesn't match folder name | Ensure folder name exactly matches DEVONthink database name |
| "No incoming group configured" | Database has no inbox | Configure incoming group in DEVONthink's database settings |
| "Script not found" | AppleScript file missing | Ensure scripts/devonthink_importer.scpt exists |
| "Config file not found" | No config/config.json | Create config/config.json or specify path with --config |

### Files Not Being Processed

1. Check the log file for errors
2. Ensure DEVONthink is running
3. Verify the file has a `.pdf` extension
4. Make sure the file isn't still downloading (`.download` files are ignored)
5. Run with `--log-level DEBUG` for more details

## Development

### Running Tests

```bash
uv run python -m pytest tests/ -v
```

### Project Structure

```
rap_importer_plugin/
├── config/
│   └── config.json             # Configuration file
├── main.py                     # Entry point
├── src/rap_importer_plugin/   # Python package
│   ├── cli.py                 # Command-line parsing
│   ├── config.py              # Configuration loading
│   ├── executor.py            # Script execution
│   ├── logging_config.py      # Logging setup
│   ├── main.py                # Main application logic
│   ├── menubar.py             # macOS menu bar app
│   ├── notifications.py       # macOS notifications
│   ├── pipeline.py            # Pipeline management
│   ├── simulate.py            # Path filter simulation
│   └── watcher.py             # File watching
├── scripts/
│   ├── devonthink_importer.applescript  # Source
│   └── devonthink_importer.scpt         # Compiled
└── tests/                      # Test suite
```

## License

MIT License
