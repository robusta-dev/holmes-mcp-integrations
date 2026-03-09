# GitHub App MCP Server

A GitHub MCP server image that supports [GitHub App](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/about-creating-github-apps) installation token authentication with automatic token refresh. Also supports standard PAT authentication as a fallback.

## Architecture

```
Holmes → SSE API → Supergateway → wrapper.py → github-mcp-server stdio
                                      ↓
                              github_app_auth.py
                              (JWT → installation token)
                              (background refresh thread)
```

The wrapper generates a GitHub installation token from the App credentials, sets it as `GITHUB_PERSONAL_ACCESS_TOKEN`, and starts the official `github-mcp-server` binary. A background thread refreshes the token before it expires.

## Authentication

### GitHub App (recommended)

Set the following environment variables:

| Variable | Description |
|----------|-------------|
| `GITHUB_APP_ID` | GitHub App ID (from App settings page) |
| `GITHUB_APP_INSTALLATION_ID` | Installation ID (from URL after installing the App) |
| `GITHUB_APP_PRIVATE_KEY` | PEM private key contents (literal `\n` is auto-converted) |
| `GITHUB_APP_TOKEN_REFRESH_INTERVAL_SEC` | Token refresh interval in seconds (default: 1800) |

### Personal Access Token (fallback)

If GitHub App env vars are not set, the image falls back to standard PAT auth:

| Variable | Description |
|----------|-------------|
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub PAT with appropriate scopes |

## Usage

```bash
# GitHub App auth
docker run -d -p 8000:8000 \
  -e GITHUB_APP_ID=<APP_ID> \
  -e GITHUB_APP_INSTALLATION_ID=<INSTALLATION_ID> \
  -e GITHUB_APP_PRIVATE_KEY="$(cat /path/to/private-key.pem)" \
  us-central1-docker.pkg.dev/genuine-flight-317411/mcp/github-app-mcp:1.0.0

# PAT auth (fallback)
docker run -d -p 8000:8000 \
  -e GITHUB_PERSONAL_ACCESS_TOKEN=ghp_... \
  us-central1-docker.pkg.dev/genuine-flight-317411/mcp/github-app-mcp:1.0.0
```

## Building

```bash
./build-push.sh
```

## How It Works

1. `wrapper.py` calls `setup_github_app_auth()` which:
   - Reads `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, `GITHUB_APP_PRIVATE_KEY` from environment
   - Generates a JWT signed with the private key
   - Exchanges it for a short-lived GitHub installation token via the GitHub API
   - Sets the token as `GITHUB_PERSONAL_ACCESS_TOKEN` in the process environment
   - Starts a daemon thread that refreshes the token at the configured interval
2. `wrapper.py` then starts `github-mcp-server stdio` which reads `GITHUB_PERSONAL_ACCESS_TOKEN` from the environment
3. Since the wrapper process holds the background thread, token refreshes update the env var in-place and the child process picks up the new token on subsequent API calls

## Difference from `servers/github`

| | `servers/github` | `servers/github-app` |
|---|---|---|
| Auth | PAT only | GitHub App + PAT fallback |
| Token refresh | None (static PAT) | Automatic background refresh |
| Dependencies | None (Go binary only) | Python, PyJWT, cryptography, requests |
