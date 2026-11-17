# CVE-patched wrapper over the upstream community mcp-atlassian image.
# Tracks ROB-624 (Vanta CVE scan 2026-07-14). Rebuild when upstream ships fixed deps.
#
# Base: upstream 0.24.6, the first line that allows fastmcp 3.x (0.22.x pins fastmcp<2.15,
# which carries CVE-2026-32871 and CVE-2026-27124).
# The remaining findings are vulnerable Python deps, upgraded below via uv inside the
# app's uv-managed venv (/app/.venv, which ships without pip/uv).
FROM ghcr.io/sooperset/mcp-atlassian:0.24.6

USER root

# Refresh base OS packages (keeps openssl/libcrypto3/libssl3 at the latest Alpine 3.24 patch).
RUN apk upgrade --no-cache

# uv is not present in the runtime image; pull the static binary just for the upgrade step.
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

# Upgrade vulnerable Python deps in place (fixed versions from Trivy):
RUN uv pip install --python /app/.venv/bin/python --upgrade \
      "anyio>=4.14.2" \
      "authlib>=1.9.1" \
      "dnspython>=2.9.2" \
      "filelock>=4.2.1" \
      "joserfc>=1.7.5" \
      "msgpack>=1.2.3" \
      "oauthlib>=4.0.1" \
      "pyjwt>=2.14.0" \
      "pymdown-extensions>=11.0.1" \
      "soupsieve>=4.0.0" \
      "urllib3>=2.8.0" \
    && uv pip check --python /app/.venv/bin/python \
    && rm -f /usr/local/bin/uv

# The app runs from /app/.venv; the system pip is unused and its vendored SBOM lists stale urllib3/msgpack/setuptools
RUN rm -rf /usr/local/lib/python3.13/site-packages/pip /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
      /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13

USER app
