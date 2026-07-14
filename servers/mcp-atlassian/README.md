# mcp-atlassian (CVE-patched wrapper)

The `confluence` MCP server runs the community
[mcp-atlassian](https://github.com/sooperset/mcp-atlassian) image. Upstream releases
lag on dependency security fixes, so this directory builds a thin wrapper that patches
the CVEs flagged by Vanta while staying byte-compatible with the upstream runtime.

## What it does

`Dockerfile` bases on the newest upstream tag (`0.22.1`) and:

- runs `apk upgrade` to keep the Alpine base packages (openssl/libcrypto3/libssl3) current, and
- upgrades the vulnerable Python deps in the app's uv-managed venv via `uv`:
  `pyjwt`, `starlette`, `python-multipart`, `cryptography`.

Upstream `0.22.1` already ships Alpine 3.24 with openssl 3.5.7-r0, so the openssl CVEs
are cleared by the base; the wrapper only needs the Python bumps.

## CVEs fixed (Vanta 2026-07-14, ROB-624)

| Package | Upstream | Patched | CVE |
|---|---|---|---|
| pyjwt | 2.11.0 | 2.13.0 | CVE-2026-48526 (High) |
| starlette | 0.52.1 | 1.3.1 | CVE-2026-48818, CVE-2026-54283 (High) |
| python-multipart | 0.0.22 | 0.0.32 | CVE-2026-53539 (High) |
| cryptography | 46.0.5 | 49.0.0 | GHSA-537c-gmf6-5ccf (High) |
| openssl/libcrypto3 | 3.5.6-r0 | 3.5.7-r0 (base) | CVE-2026-34180/34181/34182/34183/42764/45445/45447/7383/9076 |

## Build & push

Published to `us-central1-docker.pkg.dev/genuine-flight-317411/mcp/mcp-atlassian:v0.22.2`
(multi-arch amd64+arm64). Handled by the repo's `build-all-mcp-servers.sh`, or manually:

```bash
docker buildx build --builder azpush --platform linux/arm64,linux/amd64 --pull --push \
  -t us-central1-docker.pkg.dev/genuine-flight-317411/mcp/mcp-atlassian:v0.22.2 .
```

## Maintenance

Revisit when upstream mcp-atlassian ships these dependency bumps itself, then drop the
wrapper and point the deployment back at the upstream tag.
