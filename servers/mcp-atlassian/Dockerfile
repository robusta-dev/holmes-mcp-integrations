# CVE-patched wrapper over the upstream community mcp-atlassian image.
# Tracks ROB-624 (Vanta CVE scan 2026-07-14). Rebuild when upstream ships fixed deps.
#
# Base: upstream 0.22.1 runs on Alpine 3.24 with openssl/libcrypto3 3.5.7-r0, which
# already clears the openssl CVEs (CVE-2026-34180/34181/34182/34183/42764/45445/45447/7383/9076).
# The remaining findings are vulnerable Python deps, upgraded below via uv inside the
# app's uv-managed venv (/app/.venv, which ships without pip/uv).
FROM ghcr.io/sooperset/mcp-atlassian:0.22.1

USER root

# Refresh base OS packages (keeps openssl/libcrypto3/libssl3 at the latest Alpine 3.24 patch).
RUN apk upgrade --no-cache

# uv is not present in the runtime image; pull the static binary just for the upgrade step.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Upgrade vulnerable Python deps in place:
#   pyjwt            CVE-2026-48526 (High)      -> >=2.13.0
#   starlette        CVE-2026-48818, 54283 (H)  -> >=1.0.0 (fix only in 1.x; mcp requires starlette>=0.27)
#   python-multipart CVE-2026-53539 (High)      -> >=0.0.27
#   cryptography     GHSA-537c-gmf6-5ccf (High) -> >=47.0.0
RUN uv pip install --python /app/.venv/bin/python --upgrade \
      "pyjwt>=2.13.0" \
      "starlette>=1.0.0" \
      "python-multipart>=0.0.27" \
      "cryptography>=47.0.0" \
    && rm -f /usr/local/bin/uv

USER app
