"""Minimal MCP client for testing servers over streamable HTTP or SSE: initialize, tools/list, tools/call.

Errors carry only the server's error message, never request headers, so tokens stay out of CI logs.
"""
import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

READY_TIMEOUT_SEC = 90
RESPONSE_TIMEOUT_SEC = 60
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                         "clientInfo": {"name": "mcp-tester", "version": "1"}}}


class McpTestError(AssertionError):
    pass


def parse_body(raw):
    data_lines = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
    return json.loads(data_lines[-1] if data_lines else raw)


def post(url, headers, payload):
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    with urllib.request.urlopen(request, timeout=RESPONSE_TIMEOUT_SEC) as response:
        return response.read().decode(), response.headers.get("Mcp-Session-Id")


def retry_until_ready(url, connect):
    deadline = time.time() + READY_TIMEOUT_SEC
    while True:
        try:
            return connect()
        except urllib.error.HTTPError as e:
            raise McpTestError(f"{url} returned HTTP {e.code}: {e.read().decode()[:500]}")
        except (urllib.error.URLError, ConnectionError) as e:
            if time.time() > deadline:
                raise McpTestError(f"server at {url} not ready after {READY_TIMEOUT_SEC}s: {e}")
            time.sleep(2)


class HttpSession:
    """Streamable HTTP: every request is a POST to one URL, answered in the response body."""

    def __init__(self, url, token=None):
        self.url = url
        self.headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        raw, session_id = retry_until_ready(url, lambda: post(url, self.headers, INITIALIZE))
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id

    def request(self, payload):
        raw, _ = post(self.url, self.headers, payload)
        return parse_body(raw) if raw.strip() else None


def read_sse_events(stream, events):
    event = "message"
    for line in stream:
        line = line.decode().rstrip("\n")
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            events.put((event, line[5:].strip()))
            event = "message"


class SseSession:
    """SSE: responses arrive on a GET /sse stream, requests are POSTed to the endpoint it announces."""

    def __init__(self, url):
        self.events = queue.Queue()
        stream = retry_until_ready(url, lambda: urllib.request.urlopen(url, timeout=RESPONSE_TIMEOUT_SEC))
        threading.Thread(target=read_sse_events, args=(stream, self.events), daemon=True).start()
        event, endpoint = self.events.get(timeout=RESPONSE_TIMEOUT_SEC)
        if event != "endpoint":
            raise McpTestError(f"expected an endpoint event from {url}, got {event}")
        self.url = urllib.parse.urljoin(url, endpoint)
        self.headers = {"Content-Type": "application/json"}
        self.request(INITIALIZE)

    def request(self, payload):
        post(self.url, self.headers, payload)
        if "id" not in payload:
            return None
        deadline = time.time() + RESPONSE_TIMEOUT_SEC
        while time.time() < deadline:
            _, data = self.events.get(timeout=max(deadline - time.time(), 0.1))
            message = json.loads(data)
            if message.get("id") == payload["id"]:
                return message
        raise McpTestError(f"no response to {payload['method']} within {RESPONSE_TIMEOUT_SEC}s")


def call_tool(session, tool, arguments):
    """Check the tool is listed, call it and return its result."""
    session.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    tools = session.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tool_names = {listed["name"] for listed in tools["result"]["tools"]}
    if tool not in tool_names:
        raise McpTestError(f"{tool} missing from tools/list: {sorted(tool_names)}")

    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    response = session.request(call)
    if "error" in response:
        raise McpTestError(f"{tool}: {response['error'].get('message', '')[:300]}")
    if response["result"].get("isError"):
        raise McpTestError(f"{tool}: {response['result']['content'][0].get('text', '')[:300]}")
    return response["result"]
