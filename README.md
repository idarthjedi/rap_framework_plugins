# RAP Importer

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
- **Two execution modes**:
  - `--background` (default): Continuous watching with macOS menu bar icon
  - `--runonce`: Process existing files and exit (great for cron jobs)
- **Menu bar app**: Shows running status, file count, and easy quit access
- **Retry logic**: Failed files are retried up to N times with configurable delays
- **macOS notifications**: Get notified of errors (and optionally successes)
- **Flexible logging**: DEBUG/TRACE levels with file rotation

## Prerequisites

- **macOS** (tested on macOS Sonoma)
- **Python 3.12+**
- **DEVONthink Pro** installed
- **uv** package manager ([install](https://docs.astral.sh/uv/getting-started/installation/))

## Installation

### 1. Clone the Repository

```bash
git clone <repo-url>
cd rap_importer
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
1. Process any existing files in the watch folder
2. Start watching for new files
3. Show a menu bar icon ("RAP") with status and quit option

### Run Once Mode

Process all existing files and exit:

```bash
uv run rap-importer --runonce
```

### Command-Line Options

```
rap-importer [-h] [--background | --runonce] [--config FILE]
             [--log-level LEVEL] [--version]

Options:
  -h, --help            Show help message
  --background          Run continuously with menu bar icon (default)
  --runonce             Process existing files and exit
  --config, -c FILE     Path to config file (default: config/config.json)
  --log-level, -l LEVEL Override log level (TRACE, DEBUG, INFO, WARNING, ERROR)
  --version, -v         Show version
```

## Configuration

Configuration is stored in `config/config.json`:

```json
{
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
  },
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

| Section | Option | Default | Description |
|---------|--------|---------|-------------|
| watch | base_folder | ~/Documents/RAPPlatform-Import | Folder to watch |
| watch | file_patterns | ["*.pdf"] | File patterns to process |
| watch | ignore_patterns | ["*.download", ...] | Patterns to ignore |
| watch | stability_check_seconds | 1.0 | Delay between size checks |
| watch | stability_timeout_seconds | 60 | Max time to wait for stable file |
| pipeline | retry_count | 3 | Max retries for failed files |
| pipeline | retry_delay_seconds | 5 | Delay between retries |
| pipeline | delete_on_success | true | Delete source file after success |
| logging | level | INFO | Log level (TRACE/DEBUG/INFO/WARNING/ERROR) |
| logging | file | ~/Library/Logs/rap-importer.log | Log file path |
| notifications | enabled | true | Enable macOS notifications |
| notifications | on_error | true | Notify on errors |
| notifications | on_success | false | Notify on success |

### Script Variable Substitution

Scripts can use these variables in their args:
- `{file_path}` - Full POSIX path to the file
- `{relative_path}` - Path relative to watch folder
- `{filename}` - Just the filename
- `{database}` - First path component (database name)
- `{group_path}` - Path between database and filename

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
- **Original deleted**: Successfully imported files are moved to Trash (configurable)

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
rap_importer/
├── config/
│   └── config.json             # Configuration file
├── main.py                     # Entry point
├── src/rap_importer/          # Python package
│   ├── cli.py                 # Command-line parsing
│   ├── config.py              # Configuration loading
│   ├── executor.py            # Script execution
│   ├── logging_config.py      # Logging setup
│   ├── main.py                # Main application logic
│   ├── menubar.py             # macOS menu bar app
│   ├── notifications.py       # macOS notifications
│   ├── pipeline.py            # Pipeline management
│   └── watcher.py             # File watching
├── scripts/
│   ├── devonthink_importer.applescript  # Source
│   └── devonthink_importer.scpt         # Compiled
└── tests/                      # Test suite
```

## License

MIT License
