# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AppleScript tools for automatically importing PDF files into DEVONthink with OCR. Two scripts are available:

1. **Hierarchical Folder Importer** (`devonthink_importer.applescript`) - Stay-open app that monitors a folder tree and routes PDFs to databases/groups based on path
2. **Simple Folder Action** (`rapt_import_script.applescript`) - Traditional Folder Action for single-folder monitoring

## Architecture

### Hierarchical Folder Importer (Primary)

**devonthink_importer.applescript** - Stay-open application that:
1. Polls `~/Documents/DEVONthink-Import/` recursively every 5 seconds
2. Maps folder structure to DEVONthink databases and groups:
   - `DatabaseName/` → database "DatabaseName" (must exist)
   - `DatabaseName/Inbox/` → database's incoming group
   - `DatabaseName/GroupA/GroupB/` → creates nested groups as needed
3. Filters for completed PDFs (ignores partial downloads)
4. Waits for file size to stabilize
5. OCRs and imports to correct destination
6. Triggers smart rules, then deletes original

**Key handlers:**
- `on idle` - Main polling loop (returns `pollIntervalSeconds`)
- `scanFolderRecursively()` - Uses shell `find` for efficiency
- `parsePathComponents(filePath)` - Extracts database name, group path, isInbox flag
- `getDatabaseByName(dbName)` - Gets database reference (errors if not found)
- `getOrCreateDestinationGroup(...)` - Routes to incoming group or creates groups via `create location`

### Simple Folder Action (Legacy)

**rapt_import_script.applescript** - Folder Action with this workflow:
1. Triggered when files are added to the attached folder
2. Filters for completed PDF files (ignores `.download` and `.crdownload` partial downloads)
3. Waits for file size to stabilize (ensures download is complete)
4. Uses DEVONthink (`application id "DNtp"`) to OCR the PDF into the incoming group
5. Triggers smart rules on the imported record
6. Deletes the original file from the source folder

## Development

**Compiling and Validation:**
```bash
# Compile .applescript to .scpt (validates syntax)
osacompile -o devonthink_importer.scpt devonthink_importer.applescript

# Decompile .scpt back to readable text
osadecompile devonthink_importer.scpt
```

If `osacompile` succeeds with no output, the script syntax is valid.

**Creating the Stay-Open Application:**
1. Open `devonthink_importer.applescript` in Script Editor.app
2. File > Export > File Format: Application
3. Check "Stay open after run handler"
4. Save as `DEVONthink-Importer.app`

**Testing:**
- Create base folder: `mkdir -p ~/Documents/DEVONthink-Import/YourDatabase/Inbox`
- Launch the app
- Drop a PDF into the folder structure
- Check the log: `tail -f ~/Library/Logs/DEVONthink-Importer.log`

**Editing:**
- Edit the `.applescript` (plain text) file for version control friendly diffs
- Open `.scpt` files with Script Editor.app for GUI editing

## DEVONthink AppleScript Reference

DEVONthink exposes a rich scripting dictionary. View it with:
```bash
sdef /Applications/DEVONthink.app | less
```

**Key Properties (read-only):**
- `incoming group` - Default destination for new notes; resolves to global inbox or current database's incoming group
- `current group` - The selected group in the frontmost window
- `inbox` - The global inbox database

**OCR Command:**
```applescript
ocr file <path> to <destination>
```
- `file` - POSIX path or file URL of PDF/image
- `to` - Destination group (defaults to `incoming group` if omitted)
- Returns: The OCR'd record object

**Smart Rules:**
```applescript
perform smart rule record <record> trigger <event>
```
Events include: `import event`, `OCR event`, `convert event`, `tagging event`, etc.

**Creating Groups:**
```applescript
create location "GroupA/GroupB" in theDatabase
```
- Creates nested group hierarchy if it doesn't exist
- Returns the group reference (existing or newly created)

**Database Access:**
```applescript
set theDatabase to database "DatabaseName"
set incomingGrp to incoming group of theDatabase
```

**Common Patterns:**
```applescript
tell application id "DNtp"
    -- Import with OCR to inbox
    set theRecord to ocr file "~/Downloads/doc.pdf" to incoming group

    -- Check if record was created
    if exists theRecord then
        -- Trigger post-OCR smart rules
        perform smart rule record theRecord trigger OCR event
    end if
end tell
```

## Key Dependencies

- **DEVONthink Pro** (bundle ID: `DNtp`) - document management application
- **macOS Folder Actions** - system automation feature
