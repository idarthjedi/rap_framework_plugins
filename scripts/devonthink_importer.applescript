-- ============================================================
-- DEVONthink Import Script
-- Single-file import with OCR and duplicate detection
--
-- Called by Python watcher with:
--   osascript devonthink_importer.scpt "file_path" "relative_path"
--
-- Arguments:
--   file_path     - Full POSIX path to the PDF file
--   relative_path - Path relative to watch folder
--                   (e.g., "Liberty.University/BUSI770/Week01/file.pdf")
--   ocr_timeout   - (Optional) Seconds to wait for OCR before giving up.
--                   Defaults to defaultOCRTimeout below.
--
-- Returns:
--   "success"    - New file imported via OCR
--   "imported"   - New file imported without OCR (skipped, or OCR timed out)
--   "replicated" - Existing record replicated (duplicate detected)
--   Throws error on failure
--
-- Duplicate Detection:
--   Uses SHA-256 hash of original file stored as custom metadata "sourceHash".
--   DEVONthink's built-in contentHash changes after OCR processing, so we
--   calculate and store the original file hash before import. Future imports
--   search for records with matching sourceHash to detect duplicates.
-- ============================================================

-- Apple Event timeouts.
--
-- AppleScript waits a default of 120 seconds for a reply from another app.
-- OCR of a long document routinely exceeds that, and when it does the *wait*
-- aborts with error -1712 while DEVONthink carries on OCRing in the background.
-- The orphaned job then starves the next file's "ocr file" call, which returns
-- no record (error 1006). An explicit "with timeout" block is the only way to
-- extend the wait -- the caller's subprocess timeout never gets a say.
property defaultOCRTimeout : 300
property searchTimeout : 300

-- Skip OCR only when a PDF's existing text layer is unambiguous. Set high on
-- purpose: a needless OCR pass costs seconds, but a wrongly skipped one leaves
-- a document unsearchable, which is the expensive mistake in a research corpus.
-- Measured for reference: true scans read 0 chars/page, near-scans 13-41,
-- ambiguous 89-91, born-digital papers 146-8139.
property skipOCRAboveCharsPerPage : 1000

on run argv
	-- Validate arguments
	if (count of argv) < 2 then
		error "Missing parameters. Expected: {filePath, relativePath, [ocrTimeout]}" number 1000
	end if

	set filePath to item 1 of argv
	set relativePath to item 2 of argv

	-- Optional third argument overrides the default OCR timeout
	if (count of argv) > 2 then
		set ocrTimeout to (item 3 of argv) as number
	else
		set ocrTimeout to defaultOCRTimeout
	end if

	-- Parse relative path for database and group info
	set pathInfo to my parsePathComponents(relativePath)
	set dbName to databaseName of pathInfo
	set groupPath to groupPath of pathInfo
	set isInbox to isInbox of pathInfo
	set isRootLevel to isRootLevel of pathInfo

	-- Get database reference (must exist)
	set theDatabase to my getDatabaseByName(dbName)

	-- Get or create destination group
	set destGroup to my getOrCreateDestinationGroup(theDatabase, groupPath, isInbox, isRootLevel)

	-- Calculate SHA-256 hash of incoming file (with timing)
	set hashStart to my getMilliseconds()
	set fileHash to my calculateFileHash(filePath)
	set hashEnd to my getMilliseconds()
	my logTiming("hash", hashStart, hashEnd)

	-- Check for existing record with same content hash (with timing)
	set searchStart to my getMilliseconds()
	set existingRecord to my findRecordByHash(fileHash, theDatabase, searchTimeout)
	set searchEnd to my getMilliseconds()
	my logTiming("search", searchStart, searchEnd)

	if existingRecord is not missing value then
		-- Duplicate found: replicate to destination group
		set replicateStart to my getMilliseconds()
		tell application id "DNtp"
			replicate record existingRecord to destGroup
		end tell
		set replicateEnd to my getMilliseconds()
		my logTiming("replicate", replicateStart, replicateEnd)
		return "replicated"
	else
		-- No duplicate: OCR and import (with timing)
		set ocrStart to my getMilliseconds()
		set theRecord to missing value
		set usedOCR to false

		if my needsOCR(filePath) then
			-- Try OCR, but never let a wedged engine take the pipeline down with it.
			try
				with timeout of ocrTimeout seconds
					tell application id "DNtp"
						set theRecord to ocr file filePath to destGroup
					end tell
				end timeout
				set usedOCR to true
			on error errMsg number errNum
				if errNum is -1712 then
					-- OCR hung. The document still has to land, so import it as-is;
					-- a PDF that defeats the OCR engine is usually one that already
					-- carries the text layer OCR would have produced.
					log "OCR timed out after " & ocrTimeout & "s, importing without OCR"
					set theRecord to missing value
				else
					error errMsg number errNum
				end if
			end try
		end if

		if theRecord is missing value then
			tell application id "DNtp"
				set theRecord to import filePath to destGroup
			end tell
		end if

		set wordTotal to 0
		tell application id "DNtp"
			if exists theRecord then
				-- Store original file hash as custom metadata for future duplicate detection
				-- (DEVONthink's contentHash changes after OCR, so we preserve the original)
				add custom meta data fileHash for "sourceHash" to theRecord

				-- Trigger smart rules
				perform smart rule record theRecord trigger OCR event

				set wordTotal to word count of theRecord
			else
				error "Neither OCR nor import returned a record" number 1006
			end if
		end tell

		set ocrEnd to my getMilliseconds()
		my logTiming("ocr", ocrStart, ocrEnd)

		-- A record with no words is filed but invisible to search. That is the one
		-- outcome worth failing on: it looks identical to a healthy import in the
		-- UI, so nothing else would ever surface it.
		if wordTotal is 0 then
			error "Imported but NOT searchable (no text layer, OCR unavailable): " & filePath number 1008
		end if

		if usedOCR then
			return "success"
		else
			return "imported"
		end if
	end if
end run

-- ===================
-- TIMING UTILITIES
-- ===================

on getMilliseconds()
	-- Get current time in milliseconds using Python (reliable across macOS versions)
	set msStr to do shell script "python3 -c 'import time; print(int(time.time() * 1000))'"
	return msStr as number
end getMilliseconds

on logTiming(operation, startMs, endMs)
	-- Log timing to stderr (captured by Python executor).
	-- Must use "log", not "do shell script ... >&2": do shell script captures the
	-- shell's stderr itself and never forwards it to osascript's stderr, so the
	-- previous form emitted nothing at all.
	set elapsedMs to endMs - startMs
	set elapsedSec to elapsedMs / 1000
	log "TIMING: " & operation & "=" & elapsedSec & "s"
end logTiming

-- ===================
-- OCR DECISION
-- ===================

on pdfTextStats(filePath)
	-- Measure the text layer a PDF already carries, via scripts/pdf_text_stats.py.
	-- Returns {pageCount, textChars, charsPerPage}; all zeros if anything goes wrong,
	-- which needsOCR should read as "no text layer found".
	try
		set myFolder to POSIX path of ((path to me as text) & "::")
		set helper to myFolder & "pdf_text_stats.py"
		set statsLine to do shell script "/usr/bin/python3 " & quoted form of helper & " " & quoted form of filePath
		set AppleScript's text item delimiters to " "
		set parts to text items of statsLine
		set AppleScript's text item delimiters to ""
		if (count of parts) < 3 then return {pageCount:0, textChars:0, charsPerPage:0}
		return {pageCount:(item 1 of parts) as number, textChars:(item 2 of parts) as number, charsPerPage:(item 3 of parts) as number}
	on error
		return {pageCount:0, textChars:0, charsPerPage:0}
	end try
end pdfTextStats

on needsOCR(filePath)
	-- Decide whether this document should be sent to DEVONthink's OCR engine.
	--
	-- Why this exists: OCR on a born-digital PDF is wasted work, and on some of
	-- them it is worse than waste -- DEVONthink's OCR engine wedges outright
	-- (0% CPU, no reply, in any destination) on documents that import instantly
	-- and already expose a full text layer. One measured 24,029 words.
	--
	-- Calibration measured across this corpus (chars per page):
	--     0          true scans -- EBSCO scans, Likert_1932 (53pp), 2603.25883v1
	--     13 - 41    near-scans -- a little stray text, essentially images
	--     89 - 91    ambiguous -- thin or partial existing text layer
	--   146 - 8139   born-digital papers -- full text layer, OCR is pure cost
	--
	-- There is a natural gap between 91 and 146.
	--
	-- Policy: OCR unless the text layer is unmistakably complete. Anything
	-- uncertain -- a thin layer, a partial one, a file we could not parse --
	-- gets OCR'd. Slower, but nothing lands unsearchable by omission.
	set stats to my pdfTextStats(filePath)
	set pages to pageCount of stats
	set perPage to charsPerPage of stats

	-- Unparseable (0 pages): we cannot prove it has text, so OCR it.
	if pages is 0 then return true

	return perPage < skipOCRAboveCharsPerPage
end needsOCR

-- ===================
-- HASH UTILITIES
-- ===================

on calculateFileHash(filePath)
	-- Calculate SHA-256 hash of file using shasum command
	try
		set hashResult to do shell script "shasum -a 256 " & quoted form of filePath & " | awk '{print $1}'"
		return hashResult
	on error errMsg
		error "Failed to calculate file hash: " & errMsg number 1007
	end try
end calculateFileHash

on findRecordByHash(fileHash, theDatabase, searchTimeoutSeconds)
	-- Search database for a record with matching sourceHash custom metadata
	-- Note: DEVONthink's contentHash changes after OCR, so we store original
	-- file hash as custom metadata "sourceHash" and search by that
	with timeout of searchTimeoutSeconds seconds
		tell application id "DNtp"
			try
				-- Custom metadata fields are searchable with "md" prefix (case-insensitive)
				-- Critical: "in" must be OUTSIDE the search parentheses to scope correctly
				set searchResults to (search "mdsourcehash:" & fileHash) in root of theDatabase
				if (count of searchResults) > 0 then
					return item 1 of searchResults
				else
					return missing value
				end if
			on error
				-- Search failed, treat as no duplicate found
				return missing value
			end try
		end tell
	end timeout
end findRecordByHash

-- ===================
-- PATH PARSING
-- ===================

on parsePathComponents(relativePath)
	-- Split by "/"
	set AppleScript's text item delimiters to "/"
	set pathParts to text items of relativePath
	set AppleScript's text item delimiters to ""

	-- Validate minimum structure (at least database/file.pdf)
	if (count of pathParts) < 2 then
		error "File not in database subfolder: " & relativePath number 1001
	end if

	-- First part is database name
	set dbName to item 1 of pathParts

	-- Determine routing based on path structure
	set isInboxImport to false
	set isRootLevel to false
	set groupPathParts to {}

	if (count of pathParts) is 2 then
		-- File directly in database folder (e.g., Liberty.University/file.pdf)
		-- Route to incoming group
		set isRootLevel to true

	else if (count of pathParts) > 2 then
		-- Has subdirectories beyond database
		set secondLevel to item 2 of pathParts

		if secondLevel is "Inbox" then
			set isInboxImport to true
			-- Group path is everything after "Inbox" (excluding filename)
			if (count of pathParts) > 3 then
				set groupPathParts to items 3 thru -2 of pathParts
			end if
		else
			-- Regular path - everything between database and filename
			set groupPathParts to items 2 thru -2 of pathParts
		end if
	end if

	-- Reconstruct group path
	set AppleScript's text item delimiters to "/"
	set groupPathStr to groupPathParts as text
	set AppleScript's text item delimiters to ""

	return {databaseName:dbName, groupPath:groupPathStr, isInbox:isInboxImport, isRootLevel:isRootLevel}
end parsePathComponents

-- ===================
-- DEVONTHINK HANDLERS
-- ===================

on getDatabaseByName(dbName)
	tell application id "DNtp"
		try
			set theDatabase to database dbName
			if theDatabase is missing value then
				error "Database not found: " & dbName number 1002
			end if
			return theDatabase
		on error errMsg number errNum
			if errNum is 1002 then
				error errMsg number errNum
			else
				error "Database not found: " & dbName number 1002
			end if
		end try
	end tell
end getDatabaseByName

on getOrCreateDestinationGroup(theDatabase, groupPath, isInbox, isRootLevel)
	tell application id "DNtp"
		if isRootLevel or isInbox then
			-- Use database's incoming group
			set incomingGrp to incoming group of theDatabase
			if incomingGrp is missing value then
				error "Database has no incoming group configured: " & (name of theDatabase) number 1003
			end if

			if groupPath is "" then
				-- Direct to inbox/incoming
				return incomingGrp
			else
				-- Create subgroups within inbox
				return create location groupPath in incomingGrp
			end if
		else
			if groupPath is "" then
				-- Import to database root (shouldn't happen with current logic)
				return root of theDatabase
			else
				-- Create location creates nested groups as needed
				return create location groupPath in theDatabase
			end if
		end if
	end tell
end getOrCreateDestinationGroup
