# GitHub App MCP Server

A GitHub MCP server image that supports [GitHub App](https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/about-creating-github-apps) installation token authentication — including a single App installed on **multiple organizations** — with per-request credential routing and token caching. Also supports standard PAT authentication as a fallback.

## Architecture

```
Holmes ──streamable HTTP──▶ :8000 proxy.py ──▶ 127.0.0.1:8081 github-mcp-server http
                                 │                (native HTTP mode, per-request
                                 │                 Authorization: Bearer auth)
                                 ▼
                          github_app_auth.py
                          App JWT ─▶ GET /app/installations   (auto-discovery, 5-min refresh)
                                  ─▶ POST /app/installations/{id}/access_tokens
                                     (one token per installation, cached ~55 min)
```

- `wrapper.py` — entrypoint/supervisor: builds the token manager, launches the
  `github-mcp-server` binary in native `http` mode bound to localhost, and runs
  the proxy on port 8000 (the only externally reachable listener).
- `proxy.py` — reverse proxy. For each MCP request it picks the right GitHub
  App installation (see routing below), injects `Authorization: Bearer <token>`,
  and forwards everything else transparently (headers, SSE streaming responses).
  Client-supplied `Authorization` headers are always overwritten. Serves
  `/healthz` and `/readyz` (ready = at least one installation discovered).
- `owner_extraction.py` — determines which GitHub account a `tools/call`
  targets, from the `owner` / `org` / `organization` / `username` / `user`
  arguments, or from `org:NAME` / `user:NAME` / `owner:NAME` / `repo:OWNER/...`
  qualifiers inside search `query` strings.
- `github_app_auth.py` — installation discovery and token cache. Tokens are
  minted on demand and cached until 5 minutes before their 1-hour expiry.

## Multi-organization routing

Install the same GitHub App on every organization (or user account) you want to
reach and provide **only** `GITHUB_APP_ID` + `GITHUB_APP_PRIVATE_KEY`. The
server enumerates all installations of the App at startup and every
`GITHUB_APP_INSTALLATION_REFRESH_SEC` (default 300s), so newly installed
organizations become reachable without a restart.

Each `tools/call` is routed to the installation matching its owner argument.
Requests with no owner (initialize, tools/list) and owners the App is not
installed on use the **default installation** — the first discovered, or the
one selected via `GITHUB_APP_DEFAULT_OWNER` / `GITHUB_APP_DEFAULT_INSTALLATION_ID`.
Unknown owners therefore surface GitHub's own 404/403 back to the caller, plus
a warning in the server log.

## Authentication

### GitHub App (recommended)

| Variable | Description |
|----------|-------------|
| `GITHUB_APP_ID` | GitHub App ID (from App settings page). Required. |
| `GITHUB_APP_PRIVATE_KEY` | PEM private key contents (literal `\n` is auto-converted). Required. |
| `GITHUB_APP_INSTALLATION_ID` | **Optional.** When set, pins the server to that single installation and disables discovery (legacy single-org behavior). Omit for multi-org auto-discovery. |
| `GITHUB_APP_DEFAULT_OWNER` | Optional. Org/user login whose installation handles owner-less requests. |
| `GITHUB_APP_DEFAULT_INSTALLATION_ID` | Optional. Same as above, by installation ID (takes precedence). |
| `GITHUB_APP_INSTALLATION_REFRESH_SEC` | Optional. Installation discovery refresh interval (default: 300). |
| `GITHUB_HOST` | Optional. GitHub Enterprise hostname; used for both token minting and the MCP binary. |

`GITHUB_APP_TOKEN_REFRESH_INTERVAL_SEC` is deprecated and ignored — tokens are
minted on demand and cached until shortly before expiry.

### Personal Access Token (fallback)

If GitHub App env vars are not set, the image falls back to PAT auth: the same
proxy runs with a static token.

| Variable | Description |
|----------|-------------|
| `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub PAT with appropriate scopes |

## Usage

```bash
# GitHub App auth, multi-org (no installation ID)
docker run -d -p 8000:8000 \
  -e GITHUB_APP_ID=<APP_ID> \
  -e GITHUB_APP_PRIVATE_KEY="$(cat /path/to/private-key.pem)" \
  us-central1-docker.pkg.dev/genuine-flight-317411/mcp/github-app-mcp:2.0.0

# GitHub App auth, pinned to one installation (legacy behavior)
docker run -d -p 8000:8000 \
  -e GITHUB_APP_ID=<APP_ID> \
  -e GITHUB_APP_INSTALLATION_ID=<INSTALLATION_ID> \
  -e GITHUB_APP_PRIVATE_KEY="$(cat /path/to/private-key.pem)" \
  us-central1-docker.pkg.dev/genuine-flight-317411/mcp/github-app-mcp:2.0.0

# PAT auth (fallback)
docker run -d -p 8000:8000 \
  -e GITHUB_PERSONAL_ACCESS_TOKEN=ghp_... \
  us-central1-docker.pkg.dev/genuine-flight-317411/mcp/github-app-mcp:2.0.0
```

The MCP endpoint is `http://<host>:8000/mcp` (streamable HTTP). Paths are
forwarded verbatim to the underlying `github-mcp-server`.

## Building

```bash
./build-push.sh
```

The `github-mcp-server` binary version is pinned in the Dockerfile. When
bumping it, re-check `OWNER_ARG_KEYS` in `owner_extraction.py` against the new
release's tool argument names.

## Testing

```bash
pip install pytest responses PyJWT cryptography requests
pytest servers/github-app/
```

## Difference from `servers/github`

| | `servers/github` | `servers/github-app` |
|---|---|---|
| Auth | PAT only (header set by the client) | GitHub App (multi-org) + PAT fallback |
| Token management | None (static PAT) | Per-installation minting + caching |
| Multi-org | One token = one scope | Routes per request by owner/org |
| Dependencies | None (Go binary only) | Python, PyJWT, cryptography, requests |
