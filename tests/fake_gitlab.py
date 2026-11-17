"""Stand-in GitLab API for MCP tests until real test credentials exist (TODO: replace with a gitlab.com test project)."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PROJECT = {"id": 42, "name": "mcp-test-project", "path_with_namespace": "mcp-test/mcp-test-project",
           "default_branch": "main", "web_url": "http://fake-gitlab:8080/mcp-test/mcp-test-project"}
ROUTES = {"/api/v4/projects/42": PROJECT}


class FakeGitlabHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = ROUTES.get(self.path.split("?")[0])
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body if body is not None else {"message": "404 Not Found"}).encode())


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), FakeGitlabHandler).serve_forever()
