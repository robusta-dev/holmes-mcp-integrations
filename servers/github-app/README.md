# GitHub App MCP Server

A GitHub MCP server image that supports [GitHub App](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/about-creating-github-apps) installation token authentication, including one App installed on **multiple organizations**. Also supports standard PAT authentication as a fallback.

## Architecture

```
Holmes → :8000 wrapper.py ── per-request token ──→ 127.0.0.1:8081 github-mcp-server http
                  ↓
           github_utils.py (PyGithub: installations → tokens)
```

An installation token is scoped to one account, so `github_utils.py` discovers every installation of the App and caches a token per installation. `wrapper.py` proxies each request to the binary, choosing the token from the `owner`/`org` argument of the tool call.

## Authentication

### GitHub App (recommended)

Set the following environment variables:

| Variable | Description |
|----------|-------------|
| `GITHUB_APP_ID` | GitHub App ID (from App settings page) |
| `GITHUB_APP_PRIVATE_KEY` | PEM private key contents (literal `\n` is auto-converted) |
| `GITHUB_APP_INSTALLATION_ID` | Optional. Pins to one installation; omit it to serve every organization the App is installed on |
| `GITHUB_APP_TOKEN_REFRESH_INTERVAL_SEC` | Deprecated and ignored. Tokens are minted on demand and cached until shortly before they expire |

### Personal Access Token (fallback)

If GitHub App env vars are not set, the image falls back to standard PAT auth:

| Variable | Description |
|----------|-------------|
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub PAT with appropriate scopes |

## Usage

```bash
# GitHub App auth, every organization the App is installed on
docker run -d -p 8000:8000 \
  -e GITHUB_APP_ID=<APP_ID> \
  -e GITHUB_APP_PRIVATE_KEY="$(cat /path/to/private-key.pem)" \
  us-central1-docker.pkg.dev/genuine-flight-317411/mcp/github-app-mcp:2.0.0

# PAT auth (fallback)
docker run -d -p 8000:8000 \
  -e GITHUB_PERSONAL_ACCESS_TOKEN=ghp_... \
  us-central1-docker.pkg.dev/genuine-flight-317411/mcp/github-app-mcp:2.0.0
```

The MCP endpoint is `http://<host>:8000/mcp`; `/healthz` and `/readyz` are served by the proxy.

## Building

```bash
./build-push.sh
```

## How It Works

1. `wrapper.py` starts `github-mcp-server http` on localhost with no credentials: its `http` mode is stateless and authenticates every request from the `Authorization` header.
2. Each request to `:8000` is matched to an organization by `extract_owner()`, which reads the tool call's `owner`/`org`/`organization`/`username`/`user` argument, or an `org:`/`user:`/`repo:` qualifier in a search query. That organization's installation token is minted through PyGithub, cached until 5 minutes before it expires, and sent upstream as the `Authorization` header.
3. Requests naming no organization, or one the App is not installed on, use the first discovered installation. An organization added after startup is discovered the first time a request names it, at most once a minute.

Tests: `pip install pytest responses PyGithub && pytest servers/github-app/`

## Difference from `servers/github`

| | `servers/github` | `servers/github-app` |
|---|---|---|
| Auth | PAT only | GitHub App + PAT fallback |
| Token refresh | None (static PAT) | Minted on demand, cached per installation |
| Organizations | One per deployment | Every one the App is installed on |
| Dependencies | None (Go binary only) | Python, PyGithub, Starlette |
