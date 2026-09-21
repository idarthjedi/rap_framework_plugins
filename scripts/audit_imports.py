#!/usr/bin/env python3
"""Read-only audit: which PDFs in a folder are filed in DEVONthink.

    uv run python scripts/audit_imports.py [folder] [--since YYYY-MM-DD] [--problems-only]

The folder defaults to $RAP_BASE/RAPPlatform-Import/_Archived (RAP_BASE comes
from config/.env). Its subfolders must be DEVONthink database names, as in the
watch folder. --since keeps only files moved into the folder on or after a
date, which is how to find what a particular run archived.

Each file is reported as found, duplicate, ambiguous, trashed, missing or
skipped. The audit can only read: it refuses to call any DEVONthink tool that
could change something. See rap_importer_plugin.devonthink.audit.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pdf_text_stats import text_stats

from rap_importer_plugin.devonthink.audit import main

if __name__ == "__main__":
    env_file = Path(__file__).resolve().parent.parent / "config" / ".env"
    sys.exit(main(sys.argv[1:], text_stats=text_stats, env_file=env_file))
