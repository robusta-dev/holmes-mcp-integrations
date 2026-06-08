# Patches CVEs in upstream mcp-atlassian v0.21.2
# (Vanta asset 69f9a3e2edcf3e621ebe4143):
#   Python — app venv (/app/.venv, uv-managed):
#     - urllib3            2.6.3    -> 2.7       (CVE-2026-44431, CVE-2026-44432, HIGH)
#     - idna               3.11     -> 3.15      (CVE-2026-45409)
#     - pymdown-extensions 10.21    -> 10.21.3   (CVE-2026-46338)
#   Python — system interpreter:
#     - pip                26.0.1   -> 26.1      (CVE-2026-3219, CVE-2026-6357)
#   Alpine OS:
#     - xz                 5.8.2-r0 -> 5.8.3-r0  (CVE-2026-34743)
FROM us-central1-docker.pkg.dev/genuine-flight-317411/mcp/mcp-atlassian:v0.21.2

USER root

# Build-time-only uv binary to upgrade packages in the uv-managed venv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Python CVEs in the application virtualenv.
RUN uv pip install --python /app/.venv/bin/python --upgrade \
        "urllib3>=2.7" "idna>=3.15" "pymdown-extensions>=10.21.3"

# Python CVEs in the base-image system interpreter.
RUN uv pip install --python /usr/local/bin/python3.13 --upgrade "pip>=26.1"

# Alpine OS-level CVE. The installed package is xz-libs (origin: xz).
RUN apk upgrade --no-cache xz-libs

# Don't ship the build-time uv binary.
RUN rm -f /usr/local/bin/uv

USER app
