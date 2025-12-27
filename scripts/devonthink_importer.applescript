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
--
-- Returns:
--   "success"    - New file imported via OCR
--   "replicated" - Existing record replicated (duplicate detected)
--   Throws error on failure
--
-- Duplicate Detection:
--   Uses SHA-1 hash of original file stored as custom metadata "sourceHash".
--   DEVONthink's built-in contentHash changes after OCR processing, so we
--   calculate and store the original file hash before import. Future imports
--   search for records with matching sourceHash to detect duplicates.
-- ============================================================

on run argv
	-- Validate arguments
	if (count of argv) < 2 then
		error "Missing parameters. Expected: {filePath, relativePath}" number 1000
	end if

	set filePath to item 1 of argv
	set relativePath to item 2 of argv

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

	-- Calculate SHA-1 hash of incoming file
	set fileHash to my calculateFileHash(filePath)

	-- Check for existing record with same content hash
	set existingRecord to my findRecordByHash(fileHash, theDatabase)

	if existingRecord is not missing value then
		-- Duplicate found: replicate to destination group
		tell application id "DNtp"
			replicate record existingRecord to destGroup
		end tell
		return "replicated"
	else
		-- No duplicate: OCR and import
		tell application id "DNtp"
			set theRecord to ocr file filePath to destGroup

			if exists theRecord then
				-- Store original file hash as custom metadata for future duplicate detection
				-- (DEVONthink's contentHash changes after OCR, so we preserve the original)
				add custom meta data fileHash for "sourceHash" to theRecord

				-- Trigger smart rules
				perform smart rule record theRecord trigger OCR event
				return "success"
			else
				error "OCR did not return a record" number 1006
			end if
		end tell
	end if
end run

-- ===================
-- HASH UTILITIES
-- ===================

on calculateFileHash(filePath)
	-- Calculate SHA-1 hash of file using shasum command
	try
		set hashResult to do shell script "shasum -a 1 " & quoted form of filePath & " | awk '{print $1}'"
		return hashResult
	on error errMsg
		error "Failed to calculate file hash: " & errMsg number 1007
	end try
end calculateFileHash

on findRecordByHash(fileHash, theDatabase)
	-- Search database for a record with matching sourceHash custom metadata
	-- Note: DEVONthink's contentHash changes after OCR, so we store original
	-- file hash as custom metadata "sourceHash" and search by that
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
