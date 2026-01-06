# RAP Importer Plugin

A Python-based file watcher with configurable pipeline for automatically importing PDFs into DEVONthink with OCR. Files are routed to the correct database and group based on their folder structure.

---

## Why RAP Importer?

Manually importing PDFs into DEVONthink is tedious. RAP Importer automates the entire workflow:

- Drop files into a watched folder structure that mirrors your DEVONthink databases
- Files are automatically OCR'd and imported to the correct location
- Duplicates are detected and handled intelligently
- Smart rules are triggered after import

---

## Quick Start

**Requirements:** macOS, Python 3.12+, [UV](https://docs.astral.sh/uv/), DEVONthink Pro

```bash
# Clone and install
git clone https://github.com/idarthjedi/rap_framework_plugins.git
cd rap_importer_plugin
uv sync

# Create watch folder
mkdir -p ~/Documents/RAPPlatform-Import/YourDatabase/Inbox

# Start the watcher
uv run rap-importer
```

---

## Key Features

- **Event-driven file watching** — Reliable monitoring via watchdog
- **Configurable pipeline** — Chain AppleScript, Python, or shell scripts
- **Duplicate detection** — SHA-1 hash-based prevention of reimports
- **Path-based routing** — Folder structure determines DEVONthink destination
- **Three execution modes** — Background (default), foreground, run-once
- **Menu bar app** — Status indicator with easy access controls
- **Flexible filtering** — Include/exclude patterns per script

---

## Usage

```bash
# Background mode (default) - spawns daemon, returns to terminal
uv run rap-importer

# Foreground mode - for debugging
uv run rap-importer --foreground

# Run once - process existing files and exit
uv run rap-importer --runonce

# Simulate - test path filtering without running scripts
uv run rap-importer --simulate
```

---

## Folder Structure

```
~/Documents/RAPPlatform-Import/
├── DatabaseName/              → DEVONthink database
│   ├── Inbox/                 → Database's incoming group
│   │   └── file.pdf
│   └── GroupA/                → Group "GroupA" (auto-created)
│       └── SubGroup/          → Nested group (auto-created)
│           └── paper.pdf
```

---

## Documentation

**Full documentation is available in the [RAP Framework Wiki](https://github.com/idarthjedi/rap_framework/wiki):**

| Topic | Wiki Page |
|-------|-----------|
| Installation | [Importer Setup](https://github.com/idarthjedi/rap_framework/wiki/Importer-Setup) |
| Configuration | [Pipeline Configuration](https://github.com/idarthjedi/rap_framework/wiki/Pipeline-Configuration) |
| DEVONthink Integration | [DEVONthink Integration](https://github.com/idarthjedi/rap_framework/wiki/DEVONthink-Integration) |
| CLI Reference | [CLI Reference](https://github.com/idarthjedi/rap_framework/wiki/CLI-Reference) |
| Architecture | [System Overview](https://github.com/idarthjedi/rap_framework/wiki/System-Overview) |

**For developers:** See `CLAUDE.md` for implementation details.

---

## License

MIT License
