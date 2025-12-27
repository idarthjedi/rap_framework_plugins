"""Tests for script executor."""

from __future__ import annotations

from pathlib import Path

from rap_importer_plugin.executor import FileVariables, ScriptExecutor
from rap_importer_plugin.config import ScriptConfig


class TestFileVariables:
    """Tests for FileVariables."""

    def test_from_simple_file(self) -> None:
        """Should parse simple database/file.pdf path."""
        base = Path("/home/user/imports")
        file = Path("/home/user/imports/MyDatabase/document.pdf")

        vars = FileVariables.from_file(file, base)

        assert vars.file_path == str(file)
        assert vars.relative_path == "MyDatabase/document.pdf"
        assert vars.filename == "document.pdf"
        assert vars.database == "MyDatabase"
        assert vars.group_path == ""

    def test_from_nested_file(self) -> None:
        """Should parse nested database/group/subgroup/file.pdf path."""
        base = Path("/home/user/imports")
        file = Path("/home/user/imports/MyDatabase/GroupA/GroupB/document.pdf")

        vars = FileVariables.from_file(file, base)

        assert vars.database == "MyDatabase"
        assert vars.group_path == "GroupA/GroupB"
        assert vars.filename == "document.pdf"

    def test_from_inbox_file(self) -> None:
        """Should parse database/Inbox/subgroup/file.pdf path."""
        base = Path("/home/user/imports")
        file = Path("/home/user/imports/MyDatabase/Inbox/Project/document.pdf")

        vars = FileVariables.from_file(file, base)

        assert vars.database == "MyDatabase"
        assert vars.group_path == "Inbox/Project"

    def test_as_dict(self) -> None:
        """Should return variables as dictionary."""
        vars = FileVariables(
            file_path="/path/to/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path="",
            base_folder="/path/to/watch"
        )

        d = vars.as_dict()

        assert d["file_path"] == "/path/to/file.pdf"
        assert d["relative_path"] == "DB/file.pdf"
        assert d["filename"] == "file.pdf"
        assert d["database"] == "DB"
        assert d["group_path"] == ""
        assert d["base_folder"] == "/path/to/watch"
        assert d["log_level"] == "INFO"  # Default value

    def test_log_level_from_file(self) -> None:
        """Should include log_level in variables from from_file."""
        base = Path("/home/user/imports")
        file = Path("/home/user/imports/MyDatabase/document.pdf")

        vars = FileVariables.from_file(file, base, log_level="DEBUG")

        assert vars.log_level == "DEBUG"
        assert vars.as_dict()["log_level"] == "DEBUG"

    def test_base_folder_from_file(self) -> None:
        """Should include base_folder in variables from from_file."""
        base = Path("/home/user/imports")
        file = Path("/home/user/imports/MyDatabase/document.pdf")

        vars = FileVariables.from_file(file, base)

        assert vars.base_folder == "/home/user/imports"
        assert vars.as_dict()["base_folder"] == "/home/user/imports"


class TestScriptExecutor:
    """Tests for ScriptExecutor."""

    def test_resolve_absolute_path(self, tmp_path: Path) -> None:
        """Absolute paths should be returned as-is."""
        executor = ScriptExecutor(tmp_path)
        result = executor._resolve_path("/absolute/path/script.py")
        assert result == Path("/absolute/path/script.py")

    def test_resolve_relative_path(self, tmp_path: Path) -> None:
        """Relative paths should be resolved from project root."""
        executor = ScriptExecutor(tmp_path)
        result = executor._resolve_path("scripts/test.py")
        assert result == tmp_path / "scripts" / "test.py"

    def test_substitute_dict_args(self) -> None:
        """Should substitute variables in dict args."""
        executor = ScriptExecutor()
        vars = FileVariables(
            file_path="/path/to/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path="Group"
        )

        args = {
            "file": "{file_path}",
            "rel": "{relative_path}",
            "db": "{database}"
        }

        result = executor._substitute_args(args, vars)

        assert result["file"] == "/path/to/file.pdf"
        assert result["rel"] == "DB/file.pdf"
        assert result["db"] == "DB"

    def test_substitute_list_args(self) -> None:
        """Should substitute variables in list args."""
        executor = ScriptExecutor()
        vars = FileVariables(
            file_path="/path/to/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path=""
        )

        args = ["{file_path}", "{relative_path}"]

        result = executor._substitute_args(args, vars)

        assert result == ["/path/to/file.pdf", "DB/file.pdf"]

    def test_execute_missing_script(self, tmp_path: Path) -> None:
        """Should return error for missing script."""
        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="python",
            path="nonexistent.py"
        )
        vars = FileVariables(
            file_path="/test",
            relative_path="test",
            filename="test",
            database="test",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_execute_python_script(self, tmp_path: Path) -> None:
        """Should execute Python script successfully."""
        # Create a simple Python script
        script_path = tmp_path / "test.py"
        script_path.write_text('print("success")')

        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="python",
            path="test.py"
        )
        vars = FileVariables(
            file_path="/test",
            relative_path="test",
            filename="test",
            database="test",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert result.output == "success"

    def test_execute_python_with_args(self, tmp_path: Path) -> None:
        """Should pass args to Python script."""
        # Create a script that prints its args
        script_path = tmp_path / "test.py"
        script_path.write_text('''
import sys
print(f"file={sys.argv[2]}")
''')

        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="python",
            path="test.py",
            args=["--file", "{file_path}"]
        )
        vars = FileVariables(
            file_path="/path/to/test.pdf",
            relative_path="test.pdf",
            filename="test.pdf",
            database="",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert "file=/path/to/test.pdf" in result.output

    def test_execute_script_failure(self, tmp_path: Path) -> None:
        """Should capture script failure."""
        script_path = tmp_path / "fail.py"
        script_path.write_text('import sys; sys.exit(1)')

        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="python",
            path="fail.py"
        )
        vars = FileVariables(
            file_path="/test",
            relative_path="test",
            filename="test",
            database="test",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is False

    def test_execute_command_simple(self, tmp_path: Path) -> None:
        """Should execute a simple command."""
        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path="echo hello"
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert "hello" in result.output

    def test_execute_command_with_variable_substitution(self, tmp_path: Path) -> None:
        """Should substitute variables in command string."""
        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path="echo {filename}"
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert "file.pdf" in result.output

    def test_execute_command_with_args(self, tmp_path: Path) -> None:
        """Should append args to command."""
        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path="echo base",
            args=["extra", "{database}"]
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="TestDB",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert "TestDB" in result.output

    def test_execute_command_with_cwd(self, tmp_path: Path) -> None:
        """Should execute command in specified working directory."""
        work_dir = tmp_path / "workdir"
        work_dir.mkdir()

        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path="pwd",
            cwd=str(work_dir)
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert "workdir" in result.output

    def test_execute_command_cwd_with_tilde(self, tmp_path: Path) -> None:
        """Should expand ~ in cwd path."""
        import os

        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path="pwd",
            cwd="~"
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert os.path.expanduser("~") in result.output

    def test_execute_command_missing_cwd(self, tmp_path: Path) -> None:
        """Should fail if cwd directory does not exist."""
        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path="echo hello",
            cwd="/nonexistent/directory"
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is False
        assert "does not exist" in result.error

    def test_execute_command_with_quoted_args(self, tmp_path: Path) -> None:
        """Should handle quoted arguments in command string."""
        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path='echo "hello world"'
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert "hello world" in result.output

    def test_execute_command_invalid_parse(self, tmp_path: Path) -> None:
        """Should handle unparseable command string."""
        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path='echo "unclosed quote'
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is False
        assert "parse" in result.error.lower()

    def test_execute_command_variable_in_cwd(self, tmp_path: Path) -> None:
        """Should substitute variables in cwd path."""
        db_dir = tmp_path / "TestDB"
        db_dir.mkdir()

        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path="pwd",
            cwd=str(tmp_path) + "/{database}"
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="TestDB/file.pdf",
            filename="file.pdf",
            database="TestDB",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert "TestDB" in result.output

    def test_execute_command_with_group_path(self, tmp_path: Path) -> None:
        """Should substitute group_path variable correctly."""
        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path="echo --output-dir=/inbox/{group_path}/"
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/Folder/SubFolder/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path="Folder/SubFolder"
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert "--output-dir=/inbox/Folder/SubFolder/" in result.output

    def test_execute_command_clears_virtualenv(self, tmp_path: Path, monkeypatch) -> None:
        """Should clear VIRTUAL_ENV from environment for command type."""
        # Set VIRTUAL_ENV in the current environment
        monkeypatch.setenv("VIRTUAL_ENV", "/some/other/venv")

        executor = ScriptExecutor(tmp_path)
        # Use env command to list all environment variables
        script = ScriptConfig(
            name="test",
            type="command",
            path="env"
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path=""
        )

        result = executor.execute(script, vars)

        assert result.success is True
        # VIRTUAL_ENV should NOT be in the output
        assert "VIRTUAL_ENV=" not in result.output

    def test_execute_command_with_log_level(self, tmp_path: Path) -> None:
        """Should substitute log_level variable in command args."""
        executor = ScriptExecutor(tmp_path)
        script = ScriptConfig(
            name="test",
            type="command",
            path="echo",
            args=["--log-level={log_level}"]
        )
        vars = FileVariables(
            file_path="/test/file.pdf",
            relative_path="DB/file.pdf",
            filename="file.pdf",
            database="DB",
            group_path="",
            log_level="DEBUG"
        )

        result = executor.execute(script, vars)

        assert result.success is True
        assert "--log-level=DEBUG" in result.output
