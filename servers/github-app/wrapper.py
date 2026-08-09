#!/usr/bin/env python3
import json
import logging
import os
import subprocess
import sys
import threading

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from github_app_auth import AppTokens, TokenError, extract_owner, static_token

UPSTREAM_PORT = os.environ.get("GITHUB_MCP_UPSTREAM_PORT", "8081")
UPSTREAM = f"http://127.0.0.1:{UPSTREAM_PORT}"
SINGLE_HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
                      "trailers", "transfer-encoding", "upgrade", "host", "content-length"}


def relayable(headers):
    return {k: v for k, v in headers.items() if k.lower() not in SINGLE_HOP_HEADERS}


def rpc_error(body, message):
    logging.error(message)
    return JSONResponse({"jsonrpc": "2.0", "id": body.get("id") if isinstance(body, dict) else None,
                         "error": {"code": -32603, "message": message}}, status_code=502)


def build_app(tokens):
    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0))

    async def health(request):
        ready = request.url.path == "/healthz" or tokens.ready()
        return JSONResponse({"status": "ok" if ready else "not ready"}, 200 if ready else 503)

    async def proxy(request):
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        try:
            token = tokens.token_for(extract_owner(body))
            upstream = await client.send(client.build_request(
                request.method, UPSTREAM + request.url.path, content=raw,
                headers=relayable(request.headers) | {"authorization": f"Bearer {token}"}),
                stream=True)
        except TokenError as e:
            return rpc_error(body, f"No GitHub App installation token: {e}")
        except httpx.RequestError as e:
            return rpc_error(body, f"github-mcp-server unreachable at {UPSTREAM}: {e}")
        return StreamingResponse(upstream.aiter_raw(), upstream.status_code,
                                 relayable(upstream.headers),
                                 background=BackgroundTask(upstream.aclose))

    return Starlette(routes=[Route("/healthz", health), Route("/readyz", health),
                             Route("/{path:path}", proxy, methods=["GET", "POST", "DELETE"])])


def build_tokens():
    app_id, private_key = os.environ.get("GITHUB_APP_ID"), os.environ.get("GITHUB_APP_PRIVATE_KEY")
    pinned = os.environ.get("GITHUB_APP_INSTALLATION_ID")
    if os.environ.get("GITHUB_APP_TOKEN_REFRESH_INTERVAL_SEC"):
        logging.warning("GITHUB_APP_TOKEN_REFRESH_INTERVAL_SEC is deprecated and ignored")
    if app_id and private_key:
        tokens = AppTokens(app_id, private_key, pinned)
        tokens.token_for(None) if pinned else tokens.discover()
        return tokens
    if pat := os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
        return static_token(pat)
    sys.exit("Set GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY (GITHUB_APP_INSTALLATION_ID is optional,"
             " omit it to serve every organization the App is installed on), or "
             "GITHUB_PERSONAL_ACCESS_TOKEN.")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tokens = build_tokens()
    upstream = subprocess.Popen(
        ["github-mcp-server", "http", "--port", UPSTREAM_PORT, "--listen-host", "127.0.0.1"])
    stopping = threading.Event()

    def exit_with_upstream():
        code = upstream.wait()
        if not stopping.is_set():
            logging.error("github-mcp-server exited with code %s", code)
            os._exit(1)

    threading.Thread(target=exit_with_upstream, daemon=True).start()
    try:
        uvicorn.run(build_app(tokens), host="0.0.0.0",
                    port=int(os.environ.get("GITHUB_MCP_PROXY_PORT", "8000")), log_level="warning")
    finally:
        stopping.set()
        upstream.terminate()


if __name__ == "__main__":
    main()
