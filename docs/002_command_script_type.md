# Plan 002: Command Script Type with Working Directory Support

**Status:** Implemented
**Date:** 2025-12-26

## Summary

Added a new "command" script type to the RAP Importer pipeline that can run arbitrary shell commands (like `uv run rap scholarly_assessment`) with configurable working directories. This enables integration with external tools running in different project directories.

## Requirements

1. Support arbitrary shell commands via a new "command" script type
2. Add optional `cwd` field for specifying working directory
3. Variable substitution in command strings and cwd
4. Secure execution without shell injection vulnerabilities

## Implementation

### Files Modified

| File | Changes |
|------|---------|
| `src/rap_importer_plugin/config.py` | Added `cwd` field to ScriptConfig, added "command" to valid types |
| `src/rap_importer_plugin/executor.py` | Added `_execute_command()` method, updated `_run_subprocess()` with cwd support |
| `config/config.json` | Added Scholarly Assessment script configuration |
| `tests/test_config.py` | Added 5 new tests for config options |
| `tests/test_executor.py` | Added 12 new tests for command execution |
| `README.md` | Documented new script type and cwd field |

### Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Shell execution | `shell=False` with `shlex.split()` | Security: avoids command injection |
| Path field | Repurposed for command string | Keeps schema simple, consistent |
| Tilde expansion | In cwd and path-like args | User convenience |
| Variable substitution | In both `path` and `cwd` | Flexibility |

### Configuration Example

```json
{
  "name": "Scholarly Assessment",
  "type": "command",
  "path": "uv run rap scholarly_assessment",
  "enabled": true,
  "cwd": "~/development/anthropics/projects/research_analysis_platform",
  "args": ["{filename}", "--headless", "--output-dir=~/Obsidian/Leadership/Slip-Box/01-rap-inbox/{group_path}/"]
}
```

### Available Variables

- `{file_path}` - Full POSIX path to the file
- `{relative_path}` - Path relative to watch folder
- `{filename}` - Just the filename
- `{database}` - First path component (database name)
- `{group_path}` - Path between database and filename

## Testing

All 56 tests pass including 17 new tests for the command functionality:

- Config tests: command type validation, cwd field parsing
- Executor tests: command execution, variable substitution, cwd handling, error cases

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Missing cwd directory | Returns error before execution |
| Invalid command syntax | `shlex.split()` error returned |
| Command not found | Subprocess returns non-zero exit |
| Timeout | Existing 5-minute timeout applies |
