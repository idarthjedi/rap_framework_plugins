# DEVONthink Hierarchical Folder Importer

A stay-open AppleScript application that monitors a folder hierarchy and automatically imports PDFs into DEVONthink with OCR, routing them to the correct database and group based on the folder structure.

## Overview

Drop PDF files into a watched folder structure that mirrors your DEVONthink databases:

```
~/Documents/DEVONthink-Import/
├── Liberty.University/           → Database: "Liberty.University"
│   ├── Inbox/                    → database's incoming group
│   │   └── lecture.pdf
│   ├── BUSI770/                  → group "BUSI770" (created if needed)
│   │   └── Week 01/              → group "BUSI770/Week 01" (created if needed)
│   │       └── assignment.pdf
│   └── quick-note.pdf            → incoming group (root-level files)
```

## Prerequisites

- **macOS** (tested on macOS Sonoma)
- **DEVONthink Pro** installed
- DEVONthink databases must already exist with matching names

## Installation

### 1. Create the Base Folder

```bash
mkdir -p ~/Documents/DEVONthink-Import/
```

### 2. Create Database Subfolders

Create a subfolder for each DEVONthink database you want to use:

```bash
# Example for a database named "Liberty.University"
mkdir -p ~/Documents/DEVONthink-Import/Liberty.University/Inbox
```

### 3. Compile the Stay-Open Application

**Option A: Using Script Editor (Recommended)**

1. Open `devonthink_importer.applescript` in Script Editor.app
2. Go to **File > Export...**
3. Set **File Format** to **Application**
4. Check **Stay open after run handler**
5. Save as `DEVONthink-Importer.app`

**Option B: Using Command Line**

```bash
# Compile to .scpt first
osacompile -o DEVONthink-Importer.scpt devonthink_importer.applescript

# Then open in Script Editor and re-export as Application with "Stay open" checked
open -a "Script Editor" DEVONthink-Importer.scpt
```

> **Note:** The `osacompile` command cannot directly create stay-open applications. You must use Script Editor's Export dialog to enable the "Stay open after run handler" option.

## Configuration

### Changing the Base Folder

Edit the `baseFolderPath` property at the top of `devonthink_importer.applescript`:

```applescript
property baseFolderPath : "~/Documents/DEVONthink-Import/"
```

### Adjusting Poll Interval

The default poll interval is 5 seconds. To change it:

```applescript
property pollIntervalSeconds : 5
```

### Other Settings

```applescript
property stabilityCheckDelay : 0.5      -- Seconds between file size checks
property stabilityCheckMaxWait : 60     -- Max seconds to wait for download completion
property maxFilesPerCycle : 10          -- Max files to process per poll cycle (prevents blocking)
property maxProcessedListSize : 1000    -- Cleanup threshold for tracking list
property logEnabled : true              -- Enable/disable logging
```

## Running the Application

### Manual Launch

Double-click `DEVONthink-Importer.app` or:

```bash
open DEVONthink-Importer.app
```

The application will stay running in the background, polling for new PDFs every 5 seconds.

### Auto-Start on Login

To have the importer start automatically when you log in:

1. Open **System Settings**
2. Go to **General > Login Items**
3. Click **+** under "Open at Login"
4. Select `DEVONthink-Importer.app`

### Stopping the Application

- Right-click the app icon in the Dock and choose **Quit**
- Or use Activity Monitor to quit the process

## Folder Structure Reference

| File Location | DEVONthink Destination |
|--------------|------------------------|
| `DatabaseName/file.pdf` | Database's incoming group |
| `DatabaseName/Inbox/file.pdf` | Database's incoming group |
| `DatabaseName/Inbox/SubGroup/file.pdf` | Subgroup within incoming group (created if needed) |
| `DatabaseName/GroupA/file.pdf` | Group "GroupA" (created if needed) |
| `DatabaseName/GroupA/GroupB/file.pdf` | Group "GroupA/GroupB" (created if needed) |

### Special Behaviors

- **Database must exist**: If the database doesn't exist in DEVONthink, the file is skipped with an error logged
- **Inbox must exist**: The database's incoming group must be configured
- **Groups auto-created**: Non-Inbox groups are created automatically using DEVONthink's `create location` command
- **Smart rules triggered**: After OCR, smart rules with "OCR event" trigger are executed
- **Original deleted**: Successfully imported files are moved to Trash

## Troubleshooting

### Log File Location

```bash
cat ~/Library/Logs/DEVONthink-Importer.log
```

Or follow the log in real-time:

```bash
tail -f ~/Library/Logs/DEVONthink-Importer.log
```

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| "Database not found" | Database name doesn't match folder name | Ensure folder name exactly matches DEVONthink database name |
| "No incoming group configured" | Database has no inbox | Configure incoming group in DEVONthink's database settings |
| "File stability timeout" | File took >60s to finish downloading | Increase `stabilityCheckMaxWait` or check download |
| "Base folder not found" | Watch folder doesn't exist | Create `~/Documents/DEVONthink-Import/` |

### Files Not Being Processed

1. Check the log file for errors
2. Ensure DEVONthink is running
3. Verify the file has a `.pdf` extension
4. Make sure the file isn't still downloading (`.download` or `.crdownload` files are ignored)

### Application Not Staying Open

If the app quits immediately after launch:
- Ensure you exported with **Stay open after run handler** checked
- Check Console.app for crash logs
- Verify DEVONthink is installed

## Files in This Repository

- `devonthink_importer.applescript` - Main script source (plain text, version control friendly)
- `rapt_import_script.applescript` - Original Folder Action script (for reference)
- `CLAUDE.md` - Development documentation
- `README.md` - This file
