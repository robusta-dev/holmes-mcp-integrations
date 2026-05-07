# Patching CVEs in Robusta: Automated Workflow

This skill automates the process of identifying and patching CVE vulnerabilities in the Robusta Docker image and Python dependencies, focusing on critical, high, and medium severity issues.

## Overview

The workflow follows this systematic process:

1. **Vulnerability Scanning** - Identify all CVEs in dependencies and Docker image
2. **Severity Filtering** - Focus on critical, high, and medium severity issues
3. **Root Cause Analysis** - Determine which packages/dependencies introduce vulnerabilities
4. **Upstream Research** - Check if newer releases already include fixes
5. **Patch Implementation** - Apply fixes via dependency upgrades or Dockerfile changes
6. **Validation** - Verify CVE fixes and ensure application functionality

## Step-by-Step Process

### 1. Vulnerability Scanning

Use multiple scanning tools to identify vulnerabilities:

```bash
# Scan Docker image for vulnerabilities
docker build -t robusta:latest .
docker scout cves robusta:latest

# Scan Python dependencies for vulnerabilities
pip-audit
safety check

# Validate pyproject.toml metadata and lockfile consistency (does not perform vulnerability scanning)
poetry check
# For CVE scanning of Python dependencies, use pip-audit, safety, or poetry-audit-plugin
```

**What to extract:**
- Affected package name and version
- CVE ID and severity level
- Fixed version (if available)
- Affected version range

### 2. Severity Filtering

Process vulnerabilities in this order:
1. **Critical** - Must be fixed before release
2. **High** - Should be fixed before release
3. **Medium** - Fix when safe and non-breaking

Create a prioritized list and document each CVE:

```
CVE-XXXX-XXXXX (Critical): Package X - affects >=1.0.0,<1.2.0
  Fixed in: 1.2.5
  Status: Needs patching

CVE-YYYY-YYYYY (High): Package Y - affects >=2.0.0,<2.1.0
  Fixed in: 2.1.3
  Status: Needs patching
```

### 3. Python Dependency Patches

Two main strategies:

**Strategy A: Direct Upgrade (Preferred)**
- Check `poetry.lock` for affected packages
- Update `pyproject.toml` with patched version
- Run `poetry update package-name`
- Verify in `poetry.lock` that lock file has updated to fixed version

**Strategy B: Transitive Dependency Fix**
- Identify the parent package bringing in vulnerable version
- Upgrade parent package to one with updated dependencies
- This automatically pulls in the fixed transitive dependency


### 4. Dockerfile Patches

For system-level vulnerabilities (non-Python packages):

**Strategy A: Upgrade Base Image**
- Check if newer Python 3.11-slim image includes fixes
- Update FROM statement: `FROM python:3.11-slim` → newer version

**Strategy B: Explicit Package Installation**
- Add specific package upgrade in RUN commands
- Example: `apt-get install -y libssl3` for OpenSSL CVEs

**Strategy C: Apply Patches**
- Use patching tools for targeted fixes in builder stage
- Document with comments explaining which CVEs are fixed

### 5. Validation Checklist

✓ **CVE Verification**
- Run `docker scout cves` again on patched image
- Confirm target CVE no longer appears
- Note any remaining high/critical issues for tracking

✓ **Build Verification**
```bash
# Build the Docker image
docker build -t robusta:test .

# Verify build succeeds with no errors
echo "Build successful"
```

✓ **Functional Testing**
```bash
# Run basic smoke tests
pytest tests/ -v
```

✓ **Dependency Check**
```bash
# Verify no new vulnerabilities introduced
docker scout cves robusta:test --no-cache

# Validate pyproject.toml metadata and lockfile consistency
poetry check --lock
```

### 6. Documentation

Update these files with CVE fix details:

**Dockerfile Comments:**
```dockerfile
# Patching CVE-XXXX-XXXXX (Critical): Package X
RUN apt-get install -y package-name
```

## Key Considerations

### Python Package CVEs
- Check if vulnerability is in the installed wheel vs source
- For indirect dependencies, finding the transitive source is critical
- Use `poetry why package-name` to understand dependency relationships
- Go version matters for Go-based Python bindings (e.g., Cryptography)

### System Library CVEs
- libexpat1, libssl, libc vulnerabilities are common
- These often have fixes in newer base images
- When possible, upgrade the base Python image before manual fixes

### Testing Strategy
- Always rebuild and scan after each patch
- One CVE at a time is safer; group similar fixes together
- Document any CVEs that can't be patched with reasoning

### Breaking Changes
- Verify patched versions don't introduce breaking changes
- Check release notes and migration guides
- Run full test suite, not just smoke tests for major upgrades

## Implementation Notes

1. Work through CVEs in severity order (Critical → High → Medium)
2. For each CVE, follow the complete cycle: identify → research → patch → validate
3. Commit each logical group of fixes separately
4. Keep diagnostics available: `docker scout cves` output, dependency trees, test results
5. If a patch can't be safely applied, document why in the code comments

## Common Issues and Solutions

### Issue: Patch introduces breaking changes
**Solution:**
1. Check if breaking change is in major version bump
2. Review if dependency needs to be pinned differently
3. Consider if a workaround exists (e.g., compatibility shim)

### Issue: Transitive dependency is vulnerable
**Solution:**
1. Find which package brings it in: `poetry why vulnerable-package`
2. Update the parent package instead
3. Re-lock dependencies and verify fix

### Issue: CVE disappears after unrelated patch
**Solution:**
1. Good sign - often due to transitive dependency updates
2. Still verify with `docker scout cves` on final image
3. Update documentation to credit upstream fixes

## Verifying CVEs in Google Artifact Registry (auto-scan results)

After pushing to Artifact Registry, the auto-scan results live in the **Container Analysis API**. Use the snippet below to query them — it works with a normal `gcloud auth login` and does not require extra IAM beyond what push already gave you.

```bash
# Verify a pushed image's CVEs via the Container Analysis API
# Requires: gcloud auth login, image already pushed (digest known)

IMAGE_DIGEST="us-central1-docker.pkg.dev/<project>/<repo>/<image>@sha256:<digest>"
PROJECT="<project>"

FILTER=$(python3 -c "import urllib.parse; print(urllib.parse.quote('resourceUrl=\"https://${IMAGE_DIGEST}\" AND kind=\"VULNERABILITY\"'))")

curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://containeranalysis.googleapis.com/v1/projects/${PROJECT}/occurrences?filter=${FILTER}&pageSize=500" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
occs = data.get('occurrences', [])
print(f'Total: {len(occs)}')
for o in occs:
    cve = o.get('noteName', '').split('/')[-1]
    v = o.get('vulnerability', {})
    sev = v.get('severity', '?')
    for pi in v.get('packageIssue', []):
        pkg = pi.get('affectedPackage', '')
        ver = pi.get('affectedVersion', {}).get('fullName', '')
        fix = pi.get('fixedVersion', {}).get('fullName', '')
        paths = [fl.get('filePath') for fl in pi.get('fileLocation', [])]
        print(f'{sev:8} {cve:25} {pkg:20} {ver} -> {fix} {paths}')
"
```

**To filter for one CVE**, append `| grep CVE-XXXX-XXXXX` to the pipeline.

### Why not the obvious gcloud commands

- `gcloud artifacts docker images describe <image> --show-package-vulnerability` requires `roles/serviceusage.serviceUsageConsumer` on the project. Push permission ≠ scan-read permission, so this often 403s for users who can push.
- `gcloud artifacts docker images list-vulnerabilities` is for **on-demand scans only** — you must run `gcloud artifacts docker images scan ...` first and pass that scan's resource name. It does **not** query the auto-scan that runs after push.

### Gotchas

- Auto-scan results appear ~1–2 min after push; the `resourceUrl` must include the **digest**, not the tag.
- The scanner reads file metadata, not just OS package DBs. It picks up:
  - apk/dpkg-tracked OS packages (e.g. `py3-pip`)
  - Python dist-info under `site-packages/*.dist-info/METADATA`
  - **Bundled wheels** like `/usr/lib/python3.*/ensurepip/_bundled/pip-*.whl` — these are part of Alpine's `python3` package itself and stay flagged even after `pip install --upgrade pip`. Delete them in the same RUN that bootstraps pip.
- For Alpine images, the safest pattern for pip CVEs is:
  ```dockerfile
  RUN apk add --no-cache python3 && \
      wget -qO- https://bootstrap.pypa.io/get-pip.py | python3 - --break-system-packages && \
      rm -f /usr/lib/python3.*/ensurepip/_bundled/pip-*.whl
  ```
  This avoids the apk DB entry for `py3-pip` and removes the bundled wheel that ensurepip ships.