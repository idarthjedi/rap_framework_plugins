-- ============================================================
-- DEVONthink Hierarchical Folder Importer
-- Stay-open application for recursive folder monitoring
--
-- Maps folder structure to DEVONthink databases and groups:
--   ~/Documents/DEVONthink-Import/
--   └── DatabaseName/           → DEVONthink database "DatabaseName"
--       ├── Inbox/              → database's incoming group
--       ├── GroupA/GroupB/      → creates nested groups as needed
--       └── file.pdf            → incoming group (root-level files)
-- ============================================================

-- ===================
-- CONFIGURATION
-- ===================
property baseFolderPath : "~/Documents/DEVONthink-Import/"
property pollIntervalSeconds : 5
property stabilityCheckDelay : 0.5
property stabilityCheckMaxWait : 60
property maxProcessedListSize : 1000
property maxFilesPerCycle : 10
property logEnabled : true

-- ===================
-- STATE TRACKING
-- ===================
property processedFiles : {}
property filesInProgress : {}
property expandedBasePath : ""

-- ===================
-- LIFECYCLE HANDLERS
-- ===================

on run
	-- Initialize application
	my logMessage("=== DEVONthink Importer Started ===")

	-- Expand ~ in base folder path
	set expandedBasePath to do shell script "echo " & baseFolderPath

	-- Ensure base folder ends with /
	if expandedBasePath does not end with "/" then
		set expandedBasePath to expandedBasePath & "/"
	end if

	-- Verify base folder exists
	try
		do shell script "test -d " & quoted form of expandedBasePath
	on error
		display alert "Base folder not found" message "The folder " & expandedBasePath & " does not exist. Please create it and restart the application."
		quit
		return
	end try

	-- Verify DEVONthink is available
	try
		tell application id "DNtp" to launch
	on error
		display alert "DEVONthink not found" message "DEVONthink Pro must be installed."
		quit
		return
	end try

	-- Clear any stale state
	set processedFiles to {}
	set filesInProgress to {}

	my logMessage("Base folder: " & expandedBasePath)
	my logMessage("Poll interval: " & pollIntervalSeconds & " seconds")
end run

on idle
	try
		-- Ensure DEVONthink is running
		tell application id "DNtp" to launch

		-- Scan for PDF files recursively
		set pdfFiles to my scanFolderRecursively()

		-- Process files with per-cycle limit to prevent long blocking
		set filesProcessedThisCycle to 0

		repeat with filePath in pdfFiles
			-- Stop if we've hit the per-cycle limit
			if filesProcessedThisCycle ≥ maxFilesPerCycle then
				my logMessage("Reached max files per cycle (" & maxFilesPerCycle & "), deferring remaining files")
				exit repeat
			end if

			set filePathText to filePath as text

			-- Skip if already processed or in progress
			if filePathText is not in processedFiles and filePathText is not in filesInProgress then
				my processFile(filePathText)
				set filesProcessedThisCycle to filesProcessedThisCycle + 1
			end if
		end repeat

		-- Periodic cleanup of processed files list
		my cleanupProcessedListIfNeeded()

	on error errMsg number errNum
		my logMessage("Idle handler error: " & errMsg & " (" & errNum & ")")
	end try

	-- Return seconds until next poll
	return pollIntervalSeconds
end idle

on quit
	my logMessage("=== DEVONthink Importer Stopped ===")
	continue quit
end quit

-- ===================
-- CORE HANDLERS
-- ===================

on scanFolderRecursively()
	-- Use find command for efficient recursive search
	set shellCmd to "find " & quoted form of expandedBasePath & " -name '*.pdf' -type f 2>/dev/null | grep -v '\\.download$' | grep -v '\\.crdownload$'"

	try
		set findResult to do shell script shellCmd
		if findResult is "" then return {}

		-- Split by newlines
		set AppleScript's text item delimiters to linefeed
		set pdfFiles to text items of findResult
		set AppleScript's text item delimiters to ""

		return pdfFiles
	on error
		return {}
	end try
end scanFolderRecursively

on processFile(filePath)
	my logMessage("Processing: " & filePath)
	my startProcessingFile(filePath)

	try
		-- Step 1: Parse path to get database and group info
		set pathInfo to my parsePathComponents(filePath)
		set dbName to databaseName of pathInfo
		set groupPath to groupPath of pathInfo
		set isInbox to isInbox of pathInfo
		set isRootLevel to isRootLevel of pathInfo

		my logMessage("  Database: " & dbName & ", Group: " & groupPath & ", Inbox: " & isInbox & ", RootLevel: " & isRootLevel)

		-- Step 2: Wait for file to finish downloading
		my waitForFileStability(filePath)
		my logMessage("  File is stable")

		-- Step 3: Get database reference (must exist)
		set theDatabase to my getDatabaseByName(dbName)

		-- Step 4: Get or create destination group
		set destGroup to my getOrCreateDestinationGroup(theDatabase, groupPath, isInbox, isRootLevel)
		my logMessage("  Destination group ready")

		-- Step 5: OCR and import
		tell application id "DNtp"
			set theRecord to ocr file filePath to destGroup

			if exists theRecord then
				-- Step 6: Trigger smart rules
				perform smart rule record theRecord trigger OCR event
				my logMessage("  OCR complete, smart rules triggered")

				-- Step 7: Delete original file
				tell application "Finder"
					delete (POSIX file filePath as alias)
				end tell
				my logMessage("  Original file deleted")

				-- Mark as successfully processed
				my markFileProcessed(filePath)
			else
				error "OCR did not return a record" number 1006
			end if
		end tell

	on error errMsg number errNum
		my logMessage("  ERROR: " & errMsg & " (" & errNum & ")")
		my markFileError(filePath)
	end try
end processFile

on parsePathComponents(filePath)
	-- Remove base folder from path to get relative path
	set baseLen to length of expandedBasePath
	set relativePath to text (baseLen + 1) thru -1 of filePath

	-- Split by "/"
	set AppleScript's text item delimiters to "/"
	set pathParts to text items of relativePath
	set AppleScript's text item delimiters to ""

	-- Validate minimum structure (at least database/file.pdf)
	if (count of pathParts) < 2 then
		error "File not in database subfolder: " & filePath number 1001
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

-- ===================
-- FILE TRACKING
-- ===================

on startProcessingFile(filePath)
	if filePath is not in filesInProgress then
		set end of filesInProgress to filePath
	end if
end startProcessingFile

on markFileProcessed(filePath)
	-- Remove from in-progress
	set newInProgress to {}
	repeat with f in filesInProgress
		if (f as text) is not equal to (filePath as text) then
			set end of newInProgress to (f as text)
		end if
	end repeat
	set filesInProgress to newInProgress

	-- Add to processed
	set end of processedFiles to filePath
end markFileProcessed

on markFileError(filePath)
	-- Remove from in-progress (allows retry next cycle)
	set newInProgress to {}
	repeat with f in filesInProgress
		if (f as text) is not equal to (filePath as text) then
			set end of newInProgress to (f as text)
		end if
	end repeat
	set filesInProgress to newInProgress
end markFileError

on cleanupProcessedListIfNeeded()
	if (count of processedFiles) > maxProcessedListSize then
		-- Keep only the most recent half
		set halfSize to (maxProcessedListSize / 2) as integer
		set processedFiles to items halfSize thru -1 of processedFiles
		my logMessage("Cleaned up processed files list")
	end if
end cleanupProcessedListIfNeeded

-- ===================
-- UTILITIES
-- ===================

on waitForFileStability(filePath)
	set startTime to current date
	set lastSize to -1
	set currentSize to 0

	repeat
		-- Check timeout
		set elapsedSeconds to ((current date) - startTime)
		if elapsedSeconds > stabilityCheckMaxWait then
			error "File stability timeout: " & filePath number 1004
		end if

		-- Get current file size
		try
			set currentSize to (do shell script "stat -f%z " & quoted form of filePath) as integer
		on error
			error "File no longer accessible: " & filePath number 1005
		end try

		-- Check if stable (same size twice in a row and non-zero)
		if lastSize is equal to currentSize and currentSize > 0 then
			return true
		end if

		set lastSize to currentSize
		delay stabilityCheckDelay
	end repeat
end waitForFileStability

on logMessage(message)
	if not logEnabled then return

	set timestamp to do shell script "date '+%Y-%m-%d %H:%M:%S'"
	set logLine to timestamp & " - " & message

	-- Get log file path
	set logFile to do shell script "echo ~/Library/Logs/DEVONthink-Importer.log"

	-- Write to log file
	try
		do shell script "echo " & quoted form of logLine & " >> " & quoted form of logFile
	end try

	-- Also log to system for debugging
	log logLine
end logMessage
