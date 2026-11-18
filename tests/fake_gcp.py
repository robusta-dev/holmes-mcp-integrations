"""Stand-in Google Cloud APIs for MCP tests until real test credentials exist (TODO: replace with a GCP test project).

Plain HTTP on 8080 serves Resource Manager for gcloud (via CLOUDSDK_API_ENDPOINT_OVERRIDES_*).
HTTPS on 443 serves the googleapis REST clients (via GOOGLE_CLOUD_UNIVERSE_DOMAIN=fake.test): the
service account token exchange on oauth2.fake.test and Cloud Logging on logging.fake.test.
"""
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PROJECT_ID = "mcp-test-project"
ROUTES = {
    f"/v1/projects/{PROJECT_ID}": {"projectId": PROJECT_ID, "name": "MCP Test Project", "lifecycleState": "ACTIVE"},
    f"/v2/projects/{PROJECT_ID}/logs": {"logNames": [f"projects/{PROJECT_ID}/logs/mcp-test-log"]},
}
TOKEN = {"access_token": "fake", "expires_in": 3600, "token_type": "Bearer"}


class FakeGcpHandler(BaseHTTPRequestHandler):
    def reply(self, body):
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body if body is not None else {"error": {"code": 404}}).encode())

    def do_GET(self):
        self.reply(ROUTES.get(self.path.split("?")[0]))

    def do_POST(self):
        self.reply(TOKEN if self.path == "/token" else None)


if __name__ == "__main__":
    https = HTTPServer(("0.0.0.0", 443), FakeGcpHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/mcp-test/server.crt", "/mcp-test/server.key")
    https.socket = context.wrap_socket(https.socket, server_side=True)
    threading.Thread(target=https.serve_forever, daemon=True).start()
    HTTPServer(("0.0.0.0", 8080), FakeGcpHandler).serve_forever()
