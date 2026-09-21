"""Minimal client for the DEVONthink MCP server.

The server ships inside DEVONthink.app and speaks JSON-RPC 2.0. This client uses
only the standard library: the MCP SDK would add pydantic, httpx and anyio for
what amounts to newline-delimited JSON over a pipe.

Two transports sit behind one interface:

- StdioTransport (default) spawns the server with --stdio, the same way Claude
  Code connects to it. No port, no credentials, and it works whether or not the
  server's HTTP mode is switched on. A session costs ~30 ms to establish
  (measured), which is noise next to an OCR run.
- HttpTransport talks to the server's HTTP mode on localhost:8420. It needs the
  bearer token from DEVONthink's MCP settings, which the settings pane can
  regenerate -- a silent failure mode for a background importer, hence not the
  default.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

DEFAULT_SERVER_BINARY = Path(
    "/Applications/DEVONthink.app/Contents/Library/LoginItems/"
    "DEVONthink MCP.app/Contents/MacOS/DEVONthink MCP"
)
DEFAULT_HTTP_URL = "http://localhost:8420/"
DEVONTHINK_MCP_CONFIG = Path(
    "~/Library/Application Support/DEVONthink/MCP/config.json"
).expanduser()
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_TIMEOUT = 60.0


class MCPError(Exception):
    """The server could not be reached, or answered with a JSON-RPC error."""


class MCPToolError(MCPError):
    """A tool ran and reported failure."""


class MCPTimeout(MCPError):
    """No reply in time. The server may still be working on the request."""


class Transport(Protocol):
    """Moves JSON-RPC messages to and from the server."""

    def start(self) -> None: ...

    def request(self, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Send a request and return the response carrying the same id."""
        ...

    def notify(self, message: dict[str, Any]) -> None:
        """Send a notification, which gets no response."""
        ...

    def close(self) -> None: ...


class StdioTransport:
    """Run the server as a child process and exchange JSON lines over its pipes."""

    def __init__(self, command: list[str] | None = None) -> None:
        self.command = command or [str(DEFAULT_SERVER_BINARY), "--stdio"]
        self.stderr_tail: deque[str] = deque(maxlen=20)
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()

    def start(self) -> None:
        self._lines = queue.Queue()
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            raise MCPError(f"Cannot start DEVONthink MCP server ({self.command[0]}): {e}") from e

        threading.Thread(target=self._pump_stdout, args=(self._proc,), daemon=True).start()
        threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True).start()

    def _pump_stdout(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self._lines.put(line)
        self._lines.put(None)  # EOF marker

    def _drain_stderr(self, proc: subprocess.Popen[str]) -> None:
        # Keep reading even when nobody needs the output: a server that logs
        # more than a pipe buffer's worth would otherwise block mid-reply.
        assert proc.stderr is not None
        for line in proc.stderr:
            self.stderr_tail.append(line.rstrip())

    def request(self, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        self._send(message)
        deadline = time.monotonic() + timeout
        while True:
            reply = self._receive(deadline, timeout)
            # Skip notifications and any late reply to a request we gave up on.
            if reply.get("id") == message["id"]:
                return reply

    def notify(self, message: dict[str, Any]) -> None:
        self._send(message)

    def _send(self, message: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError("MCP server is not running")
        try:
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
        except OSError as e:
            raise MCPError(f"MCP server stopped accepting input: {e}{self._stderr_hint()}") from e

    def _receive(self, deadline: float, timeout: float) -> dict[str, Any]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPTimeout(f"No reply from DEVONthink MCP server within {timeout:.0f}s")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise MCPTimeout(
                    f"No reply from DEVONthink MCP server within {timeout:.0f}s"
                ) from None
            if line is None:
                self._lines.put(None)  # keep reporting EOF on later calls
                raise MCPError(f"DEVONthink MCP server exited{self._stderr_hint()}")
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                logger.debug(f"Ignoring non-JSON line from MCP server: {line[:200]}")
                continue
            if isinstance(parsed, dict):
                return parsed

    def _stderr_hint(self) -> str:
        return f" (stderr: {self.stderr_tail[-1]})" if self.stderr_tail else ""

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def read_bearer_token(config_path: Path = DEVONTHINK_MCP_CONFIG) -> str | None:
    """Return the HTTP bearer token, from the environment or DEVONthink's MCP config.

    Note that the server enforces a configured token even when its settings say
    auth.required is false.
    """
    token = os.environ.get("DEVONTHINK_MCP_TOKEN")
    if token:
        return token
    try:
        return json.loads(config_path.read_text())["auth"]["bearerToken"] or None
    except (OSError, ValueError, KeyError, TypeError):
        return None


class HttpTransport:
    """Talk to the server's HTTP mode with plain JSON-RPC POSTs."""

    def __init__(self, url: str = DEFAULT_HTTP_URL, token: str | None = None) -> None:
        self.url = url
        self._token = token
        self._session_id: str | None = None

    def start(self) -> None:
        if self._token is None:
            self._token = read_bearer_token()

    def request(self, message: dict[str, Any], timeout: float) -> dict[str, Any]:
        for reply in self._post(message, timeout):
            if reply.get("id") == message["id"]:
                return reply
        raise MCPError(f"No response for request {message['id']} in HTTP reply")

    def notify(self, message: dict[str, Any]) -> None:
        self._post(message, DEFAULT_TIMEOUT)

    def _post(self, message: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(
            self.url, data=json.dumps(message).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                self._session_id = resp.headers.get("Mcp-Session-Id") or self._session_id
                body = resp.read().decode()
                content_type = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            hint = " -- bearer token missing or rejected" if e.code == 401 else ""
            raise MCPError(f"HTTP {e.code} from DEVONthink MCP server{hint}") from e
        except TimeoutError:
            raise MCPTimeout(f"No reply from DEVONthink MCP server within {timeout:.0f}s") from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                raise MCPTimeout(
                    f"No reply from DEVONthink MCP server within {timeout:.0f}s"
                ) from None
            raise MCPError(f"Cannot reach DEVONthink MCP server at {self.url}: {e.reason}") from e
        return _parse_http_body(body, content_type)

    def close(self) -> None:
        self._session_id = None


def _parse_http_body(body: str, content_type: str) -> list[dict[str, Any]]:
    """Decode a JSON or server-sent-events reply into JSON-RPC messages."""
    if not body.strip():
        return []
    if "text/event-stream" in content_type:
        payloads = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
    else:
        payloads = [body]
    messages: list[dict[str, Any]] = []
    for payload in payloads:
        parsed = json.loads(payload)
        messages.extend(parsed if isinstance(parsed, list) else [parsed])
    return messages


class MCPClient:
    """One MCP session with the DEVONthink server.

    Use as a context manager. Tool results are returned already decoded: the
    server wraps its JSON in a text content block, which call_tool unwraps.
    """

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        client_name: str = "rap-importer",
    ) -> None:
        self.transport: Transport = transport or StdioTransport()
        self.timeout = timeout
        self.client_name = client_name
        self.server_info: dict[str, Any] = {}
        self._next_id = 0
        self._stale = False

    def __enter__(self) -> MCPClient:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def connect(self) -> None:
        self.transport.start()
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "1.0"},
            },
            self.timeout,
        )
        self.server_info = result.get("serverInfo", {})
        self.transport.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._stale = False

    def close(self) -> None:
        self.transport.close()

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        """Call a tool and return its decoded result.

        Raises MCPToolError when the tool reports failure, and MCPTimeout when no
        reply arrives in time.
        """
        if self._stale:
            # The last call timed out, so the server may still be busy with it
            # (an OCR run, say) and would queue us behind that work. Start over.
            self.close()
            self.connect()
        try:
            result = self._request(
                "tools/call",
                {"name": name, "arguments": arguments or {}},
                timeout if timeout is not None else self.timeout,
            )
        except MCPTimeout:
            self._stale = True
            raise
        return _unwrap_tool_result(name, result)

    def _request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        self._next_id += 1
        reply = self.transport.request(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params},
            timeout,
        )
        if "error" in reply:
            error = reply["error"]
            detail = error.get("message", error) if isinstance(error, dict) else error
            raise MCPError(f"{method} failed: {detail}")
        return reply.get("result", {})


def _unwrap_tool_result(name: str, result: dict[str, Any]) -> Any:
    text = "\n".join(
        block.get("text", "")
        for block in result.get("content", [])
        if block.get("type") == "text"
    )
    if result.get("isError"):
        raise MCPToolError(f"{name}: {text or 'tool reported an error'}")
    if "structuredContent" in result:
        return result["structuredContent"]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
