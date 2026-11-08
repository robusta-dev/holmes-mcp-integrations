# CLAUDE.md

## Patching CVEs

Images built here run in customer clusters, so a CVE patch is not done until the image still builds and works:

1. Build the changed images (and `mcp_base_image` first if it changed) for the native arch and rescan them with the `cve-report` skill.
2. Run the automated tests in `tests/test_mcp_servers.py`: each case builds the server's Dockerfile, runs it and calls one read-only tool.

   ```bash
   pip install pytest
   pytest tests -k <server> -v
   ```

   Cases need their `MCP_TEST_*` secrets in the env and are skipped locally without them (failed under CI), so a skip is not a pass.
3. Check the "Test MCP servers" workflow passes on the PR. It runs a server's job only when that server's Dockerfile or code changes.
4. Compare against the previous tag, not just the new one: a server that starts but rejects the chart's args (e.g. `azure-cli-mcp:1.0.7` after upstream went stdio-only) is a broken release.

A server without a test case gets one added in the same PR: add a `ServerCase` to `tests/test_mcp_servers.py` and its paths to the workflow's change filter.
