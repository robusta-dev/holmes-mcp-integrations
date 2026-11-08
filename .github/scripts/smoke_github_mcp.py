#!/usr/bin/env python3
"""Smoke-test a GitHub MCP server: initialize, tools/list, then list_commits on a repo.

usage: smoke_github_mcp.py <url> <owner/repo>   (MCP_TOKEN env is sent as a Bearer token if set)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

READY_TIMEOUT_SEC = 90


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
                          "clientInfo": {"name": "ci-smoke", "version": "1"}}}
    deadline = time.time() + READY_TIMEOUT_SEC
    while True:
        try:
            return rpc(url, headers, payload)
        except urllib.error.HTTPError as e:
            sys.exit(f"FAIL: initialize at {url} returned HTTP {e.code}: {e.read().decode()[:500]}")
        except (urllib.error.URLError, ConnectionError) as e:
            if time.time() > deadline:
                sys.exit(f"FAIL: server at {url} not ready after {READY_TIMEOUT_SEC}s: {e}")
            time.sleep(2)


def main():
    url, repo = sys.argv[1], sys.argv[2]
    owner, name = repo.split("/")
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if os.environ.get("MCP_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['MCP_TOKEN']}"

    init, session_id = initialize(url, headers)
    server = init["result"]["serverInfo"]
    print(f"initialize: {server['name']} {server.get('version')}")
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    rpc(url, headers, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    tools, _ = rpc(url, headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tool_names = {tool["name"] for tool in tools["result"]["tools"]}
    print(f"tools/list: {len(tool_names)} tools")
    if "list_commits" not in tool_names:
        sys.exit(f"FAIL: list_commits missing from tools/list: {sorted(tool_names)}")

    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "list_commits", "arguments": {"owner": owner, "repo": name, "perPage": 1}}}
    result, _ = rpc(url, headers, call)
    if "error" in result:
        sys.exit(f"FAIL: list_commits on {repo}: {result['error'].get('message', '')[:300]}")
    if result["result"].get("isError"):
        sys.exit(f"FAIL: list_commits on {repo}: {result['result']['content'][0].get('text', '')[:300]}")
    if "sha" not in json.dumps(result):
        sys.exit(f"FAIL: list_commits on {repo} returned no commits")
    print(f"list_commits {repo}: OK")


if __name__ == "__main__":
    main()
