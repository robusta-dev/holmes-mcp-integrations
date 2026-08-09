"""Extract the target GitHub account (org or user) from an MCP JSON-RPC message.

The github-mcp-server tools identify the account they operate on through a
handful of argument names (``owner``, ``org``, ...) or, for search tools,
through qualifiers embedded in the query string (``org:NAME``, ``repo:OWNER/...``).
The proxy uses this to pick the GitHub App installation whose token should
authenticate the request.
"""

import re
from typing import Any, Optional

# Argument names used by github-mcp-server tools to identify an account,
# in priority order. Re-check this list when bumping the pinned binary version.
OWNER_ARG_KEYS = ("owner", "org", "organization", "username", "user")

# Search tools carry the account inside a free-text query argument.
QUERY_ARG_KEYS = ("query", "q")

# org:NAME / user:NAME / owner:NAME / repo:OWNER/NAME — first hit wins.
# GitHub logins: alphanumerics and inner hyphens.
_QUALIFIER_RE = re.compile(
    r"\b(?:org|user|owner):([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)"
    r"|\brepo:([A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
)


def _owner_from_arguments(arguments: Any) -> Optional[str]:
    if not isinstance(arguments, dict):
        return None
    for key in OWNER_ARG_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            # Tolerate "owner/repo" pasted into an owner-only field.
            return value.strip().split("/", 1)[0].lower()
    for key in QUERY_ARG_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            match = _QUALIFIER_RE.search(value)
            if match:
                return (match.group(1) or match.group(2)).lower()
    return None


def extract_owner(body: Any) -> Optional[str]:
    """Return the GitHub account login targeted by a JSON-RPC message, or None.

    Only ``tools/call`` requests carry a target account; everything else
    (initialize, tools/list, notifications) returns None and is served with
    the default installation. Batch arrays are handled by taking the first
    tools/call element.
    """
    if isinstance(body, list):
        for element in body:
            owner = extract_owner(element)
            if owner:
                return owner
        return None
    if not isinstance(body, dict) or body.get("method") != "tools/call":
        return None
    params = body.get("params")
    if not isinstance(params, dict):
        return None
    return _owner_from_arguments(params.get("arguments"))
