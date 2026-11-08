"""Minimal streamable-HTTP MCP client for testing servers: initialize, tools/list, tools/call.

Errors carry only the server's error message, never request headers, so tokens stay out of CI logs.
"""
import json
import time
import urllib.error
import urllib.request

READY_TIMEOUT_SEC = 90


class McpTestError(AssertionError):
    pass


def parse_body(raw):
    data_lines = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
    return json.loads(data_lines[-1] if data_lines else raw)


def rpc(url, headers, payload):
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read().decode()
        session_id = response.headers.get("Mcp-Session-Id")
    return (parse_body(raw) if raw.strip() else None), session_id


def initialize(url, headers):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                          "clientInfo": {"name": "mcp-tester", "version": "1"}}}
    deadline = time.time() + READY_TIMEOUT_SEC
    while True:
        try:
            return rpc(url, headers, payload)
        except urllib.error.HTTPError as e:
            raise McpTestError(f"initialize at {url} returned HTTP {e.code}: {e.read().decode()[:500]}")
        except (urllib.error.URLError, ConnectionError) as e:
            if time.time() > deadline:
                raise McpTestError(f"server at {url} not ready after {READY_TIMEOUT_SEC}s: {e}")
            time.sleep(2)


def call_tool(url, tool, arguments, token=None):
    """Initialize a session, check the tool is listed, call it and return its result."""
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    _, session_id = initialize(url, headers)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    rpc(url, headers, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    tools, _ = rpc(url, headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tool_names = {listed["name"] for listed in tools["result"]["tools"]}
    if tool not in tool_names:
        raise McpTestError(f"{tool} missing from tools/list: {sorted(tool_names)}")

    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    response, _ = rpc(url, headers, call)
    if "error" in response:
        raise McpTestError(f"{tool}: {response['error'].get('message', '')[:300]}")
    if response["result"].get("isError"):
        raise McpTestError(f"{tool}: {response['result']['content'][0].get('text', '')[:300]}")
    return response["result"]
