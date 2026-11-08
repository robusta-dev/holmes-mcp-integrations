"""Stand-in Sentry API for MCP tests until real test credentials exist (TODO: replace with a Sentry test org)."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

ORGANIZATION = {"id": "1", "slug": "mcp-test-org", "name": "MCP Test Org",
                "links": {"organizationUrl": "http://fake-sentry:8080", "regionUrl": "http://fake-sentry:8080"}}
ROUTES = {
    "/api/0/organizations/": [ORGANIZATION],
    "/api/0/users/me/regions/": {"regions": [{"name": "default", "url": "http://fake-sentry:8080"}]},
}


class FakeSentryHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ROUTES.get(self.path.split("?")[0])
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body if body is not None else {"detail": "not found"}).encode())


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), FakeSentryHandler).serve_forever()
