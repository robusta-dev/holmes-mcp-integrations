"""Reverse proxy that injects per-installation GitHub App tokens.

Sits in front of ``github-mcp-server http`` (which authenticates every request
via ``Authorization: Bearer``) and picks the right installation token for each
MCP request based on the tool call's owner/org argument. This is what lets a
single deployment serve a GitHub App installed on multiple organizations.
"""

import http.server
import json
import logging
import os
import socketserver
from typing import Any, Optional

import requests

from github_utils import extract_owner

logger = logging.getLogger(__name__)

LISTEN_PORT = int(os.environ.get("GITHUB_MCP_PROXY_PORT", "8000"))
UPSTREAM_PORT = int(os.environ.get("GITHUB_MCP_UPSTREAM_PORT", "8081"))
UPSTREAM_BASE = f"http://127.0.0.1:{UPSTREAM_PORT}"
CONNECT_TIMEOUT_SEC = 5
READ_TIMEOUT_SEC = int(os.environ.get("GITHUB_MCP_PROXY_READ_TIMEOUT", "300"))

# Standard hop-by-hop headers plus ones we recompute ourselves.
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _request_id(body: Any) -> Any:
    if isinstance(body, dict):
        return body.get("id")
    if isinstance(body, list) and body:
        return _request_id(body[0])
    return None


class MCPProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # Set by run_proxy() before the server starts.
    token_manager = None
    _session = requests.Session()

    # -- HTTP methods ---------------------------------------------------------

    def do_GET(self):
        if self.path in ("/healthz", "/readyz"):
            return self._health()
        self._forward("GET", body=None)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        self._forward("POST", body=raw)

    def do_DELETE(self):
        self._forward("DELETE", body=None)

    # -- Health ---------------------------------------------------------------

    def _health(self):
        ready = self.token_manager is not None and self.token_manager.installation_count() > 0
        if self.path == "/healthz" or ready:
            payload = b'{"status":"ok"}'
            self.send_response(200)
        else:
            payload = b'{"status":"no installations discovered"}'
            self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # -- Proxying ---------------------------------------------------------------

    def _forward(self, method: str, body: Optional[bytes]):
        parsed_body = None
        if method == "POST" and body:
            try:
                parsed_body = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                parsed_body = None  # non-JSON: upstream decides what to do with it

        owner = extract_owner(parsed_body) if parsed_body is not None else None

        try:
            installation_id = self.token_manager.resolve_installation(owner)
            token = self.token_manager.get_token(installation_id)
        except Exception as e:
            logger.error("Failed to obtain GitHub token (owner=%s): %s", owner, e)
            return self._json_rpc_error(
                502,
                _request_id(parsed_body),
                f"Failed to obtain a GitHub App installation token: {e}",
            )

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP and key.lower() != "authorization"
        }
        # Never forward a client-supplied bearer — the proxy owns auth.
        headers["Authorization"] = f"Bearer {token}"

        try:
            upstream = self._session.request(
                method,
                UPSTREAM_BASE + self.path,
                data=body,
                headers=headers,
                stream=True,
                timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC),
            )
        except requests.RequestException as e:
            logger.error("Upstream github-mcp-server unreachable: %s", e)
            return self._json_rpc_error(
                502,
                _request_id(parsed_body),
                f"github-mcp-server upstream unreachable at {UPSTREAM_BASE}: {e}",
            )

        try:
            content_type = upstream.headers.get("Content-Type", "")
            if content_type.startswith("text/event-stream"):
                self._relay_stream(upstream)
            else:
                self._relay_buffered(upstream)
        except (BrokenPipeError, ConnectionResetError):
            logger.info("Client disconnected mid-response (owner=%s)", owner)
        finally:
            upstream.close()

        logger.info(
            "%s %s owner=%s installation=%s -> %s",
            method,
            self.path,
            owner or "-",
            installation_id if installation_id is not None else "-",
            upstream.status_code,
        )

    def _relay_buffered(self, upstream: requests.Response):
        payload = upstream.content
        self.send_response(upstream.status_code)
        for key, value in upstream.headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _relay_stream(self, upstream: requests.Response):
        """Stream an SSE response chunk-by-chunk as it arrives.

        The response is re-framed with chunked transfer-encoding: a
        close-delimited body (no Content-Length, no chunking) would be
        buffered to EOF by common HTTP clients, defeating SSE.
        """
        self.send_response(upstream.status_code)
        for key, value in upstream.headers.items():
            if key.lower() in HOP_BY_HOP:
                continue
            self.send_header(key, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in upstream.iter_content(chunk_size=None):
            if chunk:
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")

    def _json_rpc_error(self, status: int, request_id: Any, message: str):
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": message},
            }
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # -- Logging ----------------------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler API
        logger.debug("%s - %s", self.address_string(), format % args)


class ProxyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_proxy(token_manager, port: int = LISTEN_PORT) -> ProxyServer:
    MCPProxyHandler.token_manager = token_manager
    server = ProxyServer(("0.0.0.0", port), MCPProxyHandler)
    logger.info("Proxy listening on :%d, forwarding to %s", port, UPSTREAM_BASE)
    return server
