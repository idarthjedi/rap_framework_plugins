-- DEVONthink - Import, OCR & Delete.applescript
-- Created by Christian Grunenberg on Fri Jun 18 2010.
-- Copyright (c) 2010-2025. All rights reserved.

on adding folder items to this_folder after receiving added_items
	try
		if (count of added_items) is greater than 0 then
			tell application id "DNtp" to launch
			repeat with theItem in added_items
				set thePath to theItem as text
				if thePath does not end with ".download:" and thePath does not end with ".crdownload:" and thePath ends with ".pdf:" then
					set lastFileSize to 0
					set currentFileSize to 1
					repeat while lastFileSize ­ currentFileSize
						delay 0.5
						set lastFileSize to currentFileSize
						set currentFileSize to size of (info for theItem)
					end repeat
					
					try
						tell application id "DNtp"
							set theRecord to ocr file thePath to incoming group
							if exists theRecord then
								perform smart rule record theRecord trigger OCR event
								tell application "Finder" to delete theItem
							end if
						end tell
					end try
				end if
			end repeat
		end if
	end try
end adding folder items to
