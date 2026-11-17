"""Stand-in Confluence Cloud API for MCP tests until real test credentials exist (TODO: replace with an Atlassian test site)."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGE = {"id": "101", "type": "page", "status": "current", "title": "MCP test page",
        "space": {"key": "MCPTEST", "name": "MCP Test"}, "version": {"number": 1},
        "body": {"storage": {"value": "<p>mcp-test-7f3a</p>", "representation": "storage"}},
        "_links": {"webui": "/spaces/MCPTEST/pages/101"}}
ROUTES = {"/wiki/rest/api/search": {"results": [{"content": PAGE, "title": PAGE["title"], "excerpt": "mcp-test-7f3a"}],
                                    "start": 0, "limit": 10, "size": 1, "totalSize": 1}}


class FakeAtlassianHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ROUTES.get(self.path.split("?")[0])
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body if body is not None else {"message": "not found"}).encode())


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), FakeAtlassianHandler).serve_forever()
