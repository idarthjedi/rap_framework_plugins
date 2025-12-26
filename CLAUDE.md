# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a macOS Folder Action AppleScript that automatically imports PDF files into DEVONthink. When PDFs are added to a watched folder, the script OCRs them and imports them to the DEVONthink inbox, then deletes the original file.

## Architecture

**rapt_import_script.scpt** - AppleScript Folder Action with this workflow:
1. Triggered when files are added to the attached folder
2. Filters for completed PDF files (ignores `.download` and `.crdownload` partial downloads)
3. Waits for file size to stabilize (ensures download is complete)
4. Uses DEVONthink (`application id "DNtp"`) to OCR the PDF into the incoming group
5. Triggers smart rules on the imported record
6. Deletes the original file from the source folder

## Development

**Installing the Folder Action:**
1. Open Script Editor.app and save the script as a Folder Action script
2. Right-click the target folder → Services → Folder Actions Setup
3. Attach `rapt_import_script.scpt` to the folder

**Testing:**
- Drop a PDF into the watched folder
- Check DEVONthink's incoming group for the OCR'd document

**Editing:**
- Edit the `.applescript` (plain text) file for version control friendly diffs
- Open `.scpt` files with Script Editor.app for GUI editing

**Compiling and Validation:**
```bash
# Compile .applescript to .scpt (also validates syntax)
osacompile -o rapt_import_script.scpt rapt_import_script.applescript

# Decompile .scpt back to readable text
osadecompile rapt_import_script.scpt
```

If `osacompile` succeeds with no output, the script syntax is valid.

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
