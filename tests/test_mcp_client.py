"""Tests for the DEVONthink MCP client, against fake servers."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from rap_importer_plugin.devonthink.mcp_client import (
    HttpTransport,
    MCPClient,
    MCPError,
    MCPTimeout,
    MCPToolError,
    StdioTransport,
    read_bearer_token,
)

# A stand-in for "DEVONthink MCP --stdio" that misbehaves in the ways a real
# server can: log noise on stdout, unsolicited notifications, heavy stderr.
FAKE_STDIO_SERVER = r'''
import json, os, sys, time

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def text_result(text, is_error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result

for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        print("server starting up (not JSON)", flush=True)
        send({"jsonrpc": "2.0", "id": mid, "result": {"serverInfo": {"name": "fake", "version": "0"}}})
    elif method == "tools/call":
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        if name == "echo":
            send({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
            send({"jsonrpc": "2.0", "id": mid, "result": text_result(json.dumps(args))})
        elif name == "pid":
            send({"jsonrpc": "2.0", "id": mid, "result": text_result(json.dumps(os.getpid()))})
        elif name == "plain":
            send({"jsonrpc": "2.0", "id": mid, "result": text_result("Record moved to trash")})
        elif name == "structured":
            send({"jsonrpc": "2.0", "id": mid, "result": {"content": [], "structuredContent": {"a": 1}}})
        elif name == "fail":
            send({"jsonrpc": "2.0", "id": mid, "result": text_result("Record not found", is_error=True)})
        elif name == "rpc_error":
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Unknown tool"}})
        elif name == "slow":
            time.sleep(args["seconds"])
            send({"jsonrpc": "2.0", "id": mid, "result": text_result("{}")})
        elif name == "noisy":
            sys.stderr.write("x" * 200_000)
            sys.stderr.flush()
            send({"jsonrpc": "2.0", "id": mid, "result": text_result('{"ok": true}')})
        elif name == "exit":
            sys.stderr.write("fatal: database unavailable\n")
            sys.stderr.flush()
            sys.exit(3)
'''


@pytest.fixture
def fake_server(tmp_path: Path) -> list[str]:
    """Command line that launches the fake stdio server."""
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_STDIO_SERVER)
    return [sys.executable, str(script)]


@pytest.fixture
def client(fake_server: list[str]) -> Iterator[MCPClient]:
    with MCPClient(StdioTransport(fake_server), timeout=10) as c:
        yield c


class TestStdioClient:
    """Tests for MCPClient over the stdio transport."""

    def test_handshake_records_server_info(self, client: MCPClient) -> None:
        """Should complete initialize despite log noise on stdout."""
        assert client.server_info == {"name": "fake", "version": "0"}

    def test_call_tool_decodes_json_text(self, client: MCPClient) -> None:
        """Should unwrap the JSON inside the text content block."""
        assert client.call_tool("echo", {"uuid": "ABC", "n": 2}) == {"uuid": "ABC", "n": 2}

    def test_skips_notifications_before_reply(self, client: MCPClient) -> None:
        """Should ignore an unsolicited notification and return the real reply."""
        assert client.call_tool("echo", {"x": 1}) == {"x": 1}
        assert client.call_tool("echo", {"x": 2}) == {"x": 2}

    def test_returns_plain_text_when_not_json(self, client: MCPClient) -> None:
        """Should return non-JSON text as-is."""
        assert client.call_tool("plain") == "Record moved to trash"

    def test_prefers_structured_content(self, client: MCPClient) -> None:
        """Should return structuredContent when the server provides it."""
        assert client.call_tool("structured") == {"a": 1}

    def test_tool_error_raises(self, client: MCPClient) -> None:
        """Should raise MCPToolError carrying the server's message."""
        with pytest.raises(MCPToolError, match="Record not found"):
            client.call_tool("fail")

    def test_rpc_error_raises(self, client: MCPClient) -> None:
        """Should raise MCPError for a JSON-RPC error response."""
        with pytest.raises(MCPError, match="Unknown tool"):
            client.call_tool("rpc_error")

    def test_heavy_stderr_does_not_deadlock(self, client: MCPClient) -> None:
        """Should drain stderr so a chatty server cannot block on a full pipe."""
        assert client.call_tool("noisy", timeout=5) == {"ok": True}

    def test_timeout_raises(self, client: MCPClient) -> None:
        """Should raise MCPTimeout when no reply arrives in time."""
        with pytest.raises(MCPTimeout):
            client.call_tool("slow", {"seconds": 5}, timeout=0.3)

    def test_reconnects_after_timeout(self, client: MCPClient) -> None:
        """Should start a fresh server session after a timed-out call."""
        pid_before = client.call_tool("pid")
        with pytest.raises(MCPTimeout):
            client.call_tool("slow", {"seconds": 5}, timeout=0.3)

        assert client.call_tool("echo", {"after": "timeout"}) == {"after": "timeout"}
        assert client.call_tool("pid") != pid_before

    def test_server_exit_raises_with_stderr(self, client: MCPClient) -> None:
        """Should report a dead server, including its last stderr line."""
        with pytest.raises(MCPError, match="exited"):
            client.call_tool("exit", timeout=5)

    def test_missing_binary_raises(self, tmp_path: Path) -> None:
        """Should raise MCPError when the server binary does not exist."""
        transport = StdioTransport([str(tmp_path / "no-such-server"), "--stdio"])
        with pytest.raises(MCPError, match="Cannot start"):
            MCPClient(transport).connect()


class _FakeHttpHandler(BaseHTTPRequestHandler):
    token = "secret"
    use_sse = False

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        if self.headers.get("Authorization") != f"Bearer {self.token}":
            self.send_response(401)
            self.end_headers()
            return
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "id" not in msg:
            self.send_response(202)
            self.end_headers()
            return
        if msg["method"] == "initialize":
            result: dict = {"serverInfo": {"name": "fake-http"}}
        else:
            args = msg["params"]["arguments"]
            result = {"content": [{"type": "text", "text": json.dumps(args)}]}
        payload = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        if self.use_sse:
            body, ctype = f"event: message\ndata: {payload}\n\n", "text/event-stream"
        else:
            body, ctype = payload, "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Mcp-Session-Id", "session-1")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def http_url() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _FakeHttpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    _FakeHttpHandler.use_sse = False


class TestHttpClient:
    """Tests for MCPClient over the HTTP transport."""

    def test_round_trip_with_token(self, http_url: str) -> None:
        """Should authenticate with the bearer token and decode results."""
        with MCPClient(HttpTransport(http_url, token="secret")) as c:
            assert c.server_info == {"name": "fake-http"}
            assert c.call_tool("echo", {"k": "v"}) == {"k": "v"}

    def test_server_sent_events_reply(self, http_url: str) -> None:
        """Should decode replies delivered as server-sent events."""
        _FakeHttpHandler.use_sse = True
        with MCPClient(HttpTransport(http_url, token="secret")) as c:
            assert c.call_tool("echo", {"sse": True}) == {"sse": True}

    def test_wrong_token_raises_401(self, http_url: str) -> None:
        """Should explain a 401 as a token problem."""
        with pytest.raises(MCPError, match="401.*bearer token"):
            MCPClient(HttpTransport(http_url, token="wrong")).connect()


class TestReadBearerToken:
    """Tests for read_bearer_token."""

    def test_reads_from_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should read auth.bearerToken from DEVONthink's MCP config."""
        monkeypatch.delenv("DEVONTHINK_MCP_TOKEN", raising=False)
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"auth": {"bearerToken": "from-file", "required": False}}))
        assert read_bearer_token(config) == "from-file"

    def test_environment_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should prefer DEVONTHINK_MCP_TOKEN over the config file."""
        monkeypatch.setenv("DEVONTHINK_MCP_TOKEN", "from-env")
        assert read_bearer_token(tmp_path / "missing.json") == "from-env"

    def test_missing_config_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return None rather than raise when there is no token."""
        monkeypatch.delenv("DEVONTHINK_MCP_TOKEN", raising=False)
        assert read_bearer_token(tmp_path / "missing.json") is None
