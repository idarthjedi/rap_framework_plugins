#!/usr/bin/env python3
"""Import a PDF into DEVONthink via the DEVONthink MCP server.

Pipeline entry point for "DEVONthink Import (MCP)" in config/config.json:

    python devonthink_importer.py <file_path> <relative_path> [ocr_timeout]
        [--transport stdio|http] [--no-enrich] [--enrich-rename]

Takes the same positional arguments as devonthink_importer.applescript, which
remains the fallback -- switching between the two is a matter of flipping
`enabled` on their config entries.

The logic lives in rap_importer_plugin.devonthink.importer. This file only
supplies pdf_text_stats, which stays here beside the AppleScript because the
AppleScript runs it under the system python3 with no access to the package.
"""

from __future__ import annotations

import sys

from pdf_text_stats import text_stats

from rap_importer_plugin.devonthink.importer import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], text_stats=text_stats))
