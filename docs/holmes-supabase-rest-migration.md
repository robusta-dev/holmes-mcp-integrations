# Holmes → REST API: Supabase Data-Layer Migration Plan

**Goal:** Holmes (holmesgpt) should stop talking to Supabase directly (PostgREST, GoTrue auth,
Postgres RPCs, Realtime WebSocket) and instead use a plain REST client against a new backend
API that we own. The backend becomes the only component that talks to the database.

All code references below are to `robusta-dev/holmesgpt` (master as of 2026-09-01). The entire
Supabase surface is funneled through one class — `SupabaseDal`
(`holmes/core/supabase_dal.py`) — plus one consumer that reaches around it for realtime:
`RealtimeWorker` (`holmes/core/conversations_worker/realtime_manager.py`). That makes this a
very tractable migration: swap the implementation behind an interface Holmes already
effectively has.

---

## 1. Complete inventory of Supabase operations

### 1.1 Direct table operations (PostgREST)

| # | DAL method (supabase_dal.py) | Table(s) | Operation | Callers |
|---|---|---|---|---|
| 1 | `get_issue_data` / `get_issue_from_db` | `Issues`, `GroupedIssues`, `Evidence` | SELECT by id; SELECT evidence by `issue_id` with `enrichment_type not.in(...)` | `plugins/toolsets/robusta/robusta.py` (fetch_finding tool), `conversations_worker/worker.py` |
| 2 | `get_skill_catalog` | `HolmesRunbooks` | SELECT (account, subject_type=RunbookCatalog, enabled) | `plugins/skills/skill_loader.py` |
| 3 | `get_skill_content` | `HolmesRunbooks` | SELECT one by `runbook_id` (enabled only) | `plugins/toolsets/skills/skills_fetcher.py` |
| 4 | `get_personal_skill_catalog` | `HolmesRunbooks` | SELECT (account, user_id, subject_type=PersonalRunbookCatalog) | `plugins/skills/skill_loader.py` |
| 5 | `get_personal_skill_content` | `HolmesRunbooks` | SELECT one (account, user_id, runbook_id, enabled) | `plugins/toolsets/skills/skills_fetcher.py` |
| 6 | `get_resource_instructions` | `HolmesRunbooks` | SELECT by (account, subject_type, subject_name) | investigation flow (`server.py`) |
| 7 | `get_global_instructions_for_account` | `HolmesRunbooks` | SELECT by (account, subject_type=Account) | `server.py`, `conversations_worker/worker.py` |
| 8 | `get_skill_hierarchy_config` | `AccountSettings` | SELECT `settings` by account (60s TTL cache) | `holmes/config.py` |
| 9 | `create_session_token` | `AuthTokens` | INSERT (returning=minimal) | via `get_ai_credentials` |
| 10 | `get_ai_credentials` | (cache over #9) | returns `(account_id, session_token)`, 23h TTL cache | `holmes/config.py`, `core/llm.py` (Robusta AI), `plugins/toolsets/robusta_platform_mcp/` |
| 11 | `upsert_holmes_status` | `HolmesStatus` | UPSERT on `(account_id, cluster_id)` | `utils/holmes_status.py` (startup) |
| 12 | `sync_toolsets` | `HolmesToolsStatus` | UPSERT on `(account_id, cluster_id, toolset_name)` + DELETE stale (`not.in` names) | `utils/holmes_sync_toolsets.py` |
| 13 | `sync_skills` | `HolmesCustomSkills` | UPSERT on `(account_id, cluster_id, skill_name)` + optional prune DELETE | `utils/holmes_sync_skills.py` |
| 14 | `record_usage_event` | `HolmesUsageEvents` | INSERT (best-effort telemetry) | `core/usage_recorder.py` |
| 15 | `has_scheduled_prompt_definitions` | `ScheduledPromptsDefinitions` | SELECT count by account | `core/scheduled_prompts/executor.py` |
| 16 | `update_run_status` | `ScheduledPromptsRuns` | UPDATE status + heartbeat by (id, account) | `scheduled_prompts/executor.py`, `scheduled_prompts/heartbeat_tracer.py` |
| 17 | `get_oauth_token` | `OAuthTokens` | SELECT by (account, provider, user_id), ordered by `updated_at` | `plugins/toolsets/mcp/oauth_token_store.py` |
| 18 | `upsert_oauth_token` | `OAuthTokens` | UPSERT on `(account_id, provider_name, signing_key_hash, user_id)` | `oauth_token_store.py` |
| 19 | `delete_oauth_token` | `OAuthTokens` | DELETE by (account, provider, user_id, signing_key_hash) | `oauth_token_store.py` |
| 20 | `get_all_oauth_tokens_for_cluster` | `OAuthTokens` | SELECT by (account, origin_cluster_id, signing_key_hash) | `oauth_token_store.py` (startup preload) |

### 1.2 Postgres RPCs (via PostgREST `/rpc/...`)

These are already server-side atomic functions, which maps 1:1 onto REST endpoints.

| # | DAL method | RPC | Semantics | Callers |
|---|---|---|---|---|
| 21 | `claim_scheduled_prompt_run` | `claim_scheduled_prompt_run` | Atomic claim of one pending run for (account, cluster, holmes_id) | `scheduled_prompts/executor.py` |
| 22 | `finish_scheduled_prompt_run` | `finish_scheduled_prompt_run` | Terminal status + result + metadata write | `scheduled_prompts/executor.py` |
| 23 | `is_realtime_enabled` | `is_realtime_enabled` | Tri-state: True / False (incl. missing RPC) / None (transport error) | `conversations_worker/worker.py` |
| 24 | `claim_n_pending_conversations` | `claim_n_pending_conversations` | Atomic batch claim, oldest-first, assignee-stamped; tenacity retry ×3 | `conversations_worker/worker.py` |
| 25 | `claim_n_pending_tool_calls` | `claim_n_pending_tool_calls` | Same, for remote tool calls | `conversations_worker/tool_call_worker.py` |
| 26 | `post_remote_tool_call_result` | `post_remote_tool_call_result` | Assignee-guarded terminal UPDATE; "mismatch/not found" is terminal (first-result-wins) | `tool_call_worker.py` |
| 27 | `post_conversation_events` | `post_conversation_events` | Append event batch, returns seq; optional compaction; "mismatch" → `ConversationReassignedError` | `conversations_worker/worker.py`, `event_publisher.py` |
| 28 | `update_conversation_status` | `update_conversation_status` | Guarded status transition (assignee + request_sequence match) | `conversations_worker/worker.py` |
| 29 | `get_conversation_events` | `get_conversation_events` | Flattened chronological events, compaction-aware (SECURITY DEFINER — Holmes has no direct SELECT under RLS) | `conversations_worker/worker.py` |

### 1.3 Auth (Supabase GoTrue)

- `sign_in` — `auth.sign_in_with_password(email, password)` → session; `set_session`, `postgrest.auth(access_token)`; returns `user_id` (used by `create_session_token` and RLS scoping).
- Credential sourcing (`__init_config` / `__connect`): Robusta UI token (base64 JSON: `store_url`, `api_key`, `account_id`, `email`, `password`) or `STORE_*` env vars; per-account anon **API key fetched from the relay** — `fetch_supabase_api_key` in `holmes/clients/robusta_client.py`, with a 24h `KEY_CACHE`.
- JWT lifecycle: monkey-patched `SyncQueryRequestBuilder.execute` retries once after re-sign-in on `PGRST301`/"expired" (`patch_postgrest_execute`).
- Transport hardening owned client-side today: `SupabaseRetryTransport` (HTTP/1.1 forced for thread safety, retry ×3 on `RemoteProtocolError`), CA-bundle/proxy resolution, `pre_select` monkey-patch.

### 1.4 Realtime (WebSocket — outside the DAL)

`RealtimeWorker` (`conversations_worker/realtime_manager.py`):
- Connects to `wss://<store_url>/realtime/v1` with the Supabase anon key, then `set_auth(user_jwt)` taken from `dal.client.auth.get_session()`.
- Two modes: **Broadcast** channel per (account, cluster) — new-pending-conversation nudges — or legacy **Postgres Changes** on `Conversations`.
- Owns reconnect/backoff, health ticks, JWT near-expiry refresh via `dal.sign_in()`, SSL/proxy patches.
- Push is an optimization only: the worker already falls back to polling (`claim_n_pending_conversations` loop) when realtime is off/unhealthy.

### 1.5 Adjacent facts that shape the design

- `dal.enabled`, `dal.account_id`, `dal.cluster`, `dal.user_id`, `dal.url` are read directly all over (`server.py`, workers, toolsets) — the replacement must keep this surface.
- Frontend feedback writes (`record_feedback` RPC) do **not** go through Holmes (noted at supabase_dal.py:1181) — out of scope here, but the FE will need the same treatment eventually.
- Tests to carry over: `tests/core/test_supabase_dal*.py`, `tests/core/conversations_worker/*` (incl. a DAL contract test), `tests/llm/utils/mock_dal.py`.

---

## 2. Target architecture

```
Holmes (cluster)  --HTTPS-->  Holmes Store API (new backend)  --->  Postgres
     |                              |
     +--- SSE / WS (push nudges) ---+        (Supabase Realtime retired for Holmes)
```

- **One bearer credential**: Holmes authenticates with the existing Robusta token (or an
  API key derived from it) via `Authorization: Bearer`. The backend resolves account_id and
  enforces scoping server-side — replacing GoTrue email/password sign-in, JWT refresh,
  relay key fetch, and RLS-as-client-concern entirely.
- **Domain endpoints, not generic table access.** The DAL methods are already domain
  operations; the REST API mirrors them 1:1 so Holmes-side call sites don't change shape.
- **Server-side invariants stay server-side**: the claim/post RPCs keep their atomicity —
  they just get an HTTP endpoint in front of the same SQL (or the logic moves into the
  backend service in a transaction).

### 2.1 Proposed REST contract (v1)

All routes prefixed `/api/holmes/v1`, account resolved from the auth token; `cluster_id` a
header (`X-Cluster-Id`) or query param. Errors: structured JSON with a machine-readable
`code` (see §3.4 — the worker branches on reassignment vs. transient).

**Issues & evidence**
- `GET /issues/{issue_id}` → issue row (backend does the Issues → GroupedIssues promotion for prometheus sources)
- `GET /issues/{issue_id}/evidence` → filtered evidence (blacklist + gz-unzip can move server-side, or stay client-side initially)

**Skills / runbooks / instructions**
- `GET /skills/catalog`
- `GET /skills/{skill_id}`
- `GET /skills/personal/catalog?user_id=...`
- `GET /skills/personal/{skill_id}?user_id=...`
- `GET /instructions/resource?subject_type=...&subject_name=...`
- `GET /instructions/global`
- `GET /account/settings/skill-hierarchy`

**Session tokens / AI credentials**
- `POST /session-tokens` → `{token}` (server generates + inserts; Holmes keeps its 23h cache)

**Status & sync**
- `PUT /status` (holmes status upsert)
- `PUT /toolsets` (full-set sync: upsert + prune in one transaction — better than today's two calls)
- `PUT /custom-skills` (body includes `prune: bool`, same transactional collapse)

**Telemetry**
- `POST /usage-events` (fire-and-forget; consider batching later)

**Scheduled prompts**
- `GET /scheduled-prompts/definitions/exists`
- `POST /scheduled-prompts/runs/claim` `{holmes_id}`
- `PATCH /scheduled-prompts/runs/{run_id}` `{status, msg?}` (also the heartbeat)
- `POST /scheduled-prompts/runs/{run_id}/finish` `{status, result, definition_id, version, metadata}`

**Conversations worker**
- `POST /conversations/claim` `{holmes_id, limit}`
- `PATCH /conversations/{id}/status` `{status, assignee, request_sequence}` → 409 `code=REASSIGNED` on mismatch
- `POST /conversations/{id}/events` `{assignee, request_sequence, events[], compact}` → `{seq}` / 409 REASSIGNED
- `GET /conversations/{id}/events?include_compacted=&min_seq=`
- `POST /tool-calls/claim` `{holmes_id, limit}`
- `POST /tool-calls/{id}/result` `{assignee, status, tool_response}` → 409 `code=REJECTED` (terminal, first-result-wins)

**OAuth tokens**
- `GET /oauth-tokens/{provider}?user_id=&signing_key_hash=` (backend can keep the deliberate mismatch-warning behavior by returning stored hashes)
- `PUT /oauth-tokens/{provider}`
- `DELETE /oauth-tokens/{provider}?user_id=&signing_key_hash=`
- `GET /oauth-tokens?origin_cluster_id=&signing_key_hash=` (startup preload)

**Push (realtime replacement)**
- `GET /notifications/stream` — SSE (or WebSocket) emitting `conversation_submitted` /
  `tool_call_submitted` nudges per (account, cluster). Contract mirrors today's broadcast
  usage: it's only a wake-up signal; claiming still happens via the claim endpoints, and
  polling remains the fallback. `GET /notifications/capabilities` replaces `is_realtime_enabled`.

### 2.2 Holmes-side changes

1. **Extract an interface**: `HolmesStoreDal` (Protocol/ABC) with exactly the 29 operations
   above plus the attribute surface (`enabled`, `account_id`, `cluster`, `user_id`).
   `tests/core/conversations_worker/test_dal_contract.py` already points this direction.
2. **New `RestStoreDal`** implementing it with `httpx` (keep: CA bundle/proxy resolution,
   HTTP/1.1 + `RemoteProtocolError` retry transport — both copy over nearly verbatim;
   drop: postgrest/gotrue/realtime deps, both monkey-patches, JWT re-sign-in machinery).
   Keep the per-method tenacity retry/error-swallowing semantics identical (§3.4).
3. **`RestRealtimeWorker`** (or a transport strategy inside `RealtimeWorker`): SSE consumer
   with the same reconnect/backoff/health contract; the worker's poll fallback is untouched.
4. **Selection & rollout flag**: config/env (e.g. `STORE_MODE=supabase|rest`, plus
   `STORE_REST_URL`) choosing the implementation at startup. The Robusta token can carry
   the new API URL for zero-config cutover of existing installs.

---

## 3. Migration plan (phases)

### Phase 0 — Contract & scaffolding (backend + holmes in parallel)
- Freeze the v1 OpenAPI spec from §2.1; generate/publish a spec both repos test against.
- Extract the `HolmesStoreDal` interface in holmesgpt; make all call sites depend on it
  (mechanical — they already call only these methods). Ship this refactor alone first.
- Build a contract-test suite (shared fixtures) that runs against both implementations.

### Phase 1 — Backend API
- Implement read endpoints + write endpoints over the same Postgres, reusing the existing
  RPC functions where they exist (thin HTTP wrapper) — no schema changes required.
- AuthN/Z: bearer-token middleware resolving account; per-account rate limits; enforce the
  scoping rules that RLS enforces today (personal skills by user_id, assignee guards, etc.).
  **This is the highest-risk area: RLS policies must be re-expressed as explicit WHERE
  clauses/authorization checks in the backend.** Audit every policy on the 13 tables + 9 RPCs.
- Observability: per-endpoint metrics, structured error codes, request ids.

### Phase 2 — Holmes REST client
- Implement `RestStoreDal` + `RestRealtimeWorker` behind the flag.
- Port the DAL unit tests to contract tests; add golden tests for error-semantics parity
  (see §3.4) and for `mock_dal.py` in the LLM eval harness.

### Phase 3 — Validation & rollout
- **Shadow mode** (optional but cheap for reads): run REST reads alongside Supabase in a
  canary cluster, diff results, log mismatches. Writes are validated in a staging account
  (dual-writing claim RPCs is unsafe — don't).
- Rollout order, lowest risk → highest:
  1. Telemetry & sync writes (usage events, holmes status, toolsets, custom skills)
  2. Reads (issues/evidence, skills, instructions, account settings, oauth reads)
  3. Session tokens / AI credentials
  4. Scheduled prompts (claim/heartbeat/finish)
  5. Conversations + tool-call workers (claims, events, statuses) + SSE push
- Flag flip per stage per canary account → all accounts. Keep `SupabaseDal` for ≥1 release
  as the rollback path.

### Phase 4 — Cleanup
- Remove supabase/postgrest/gotrue/realtime deps from `pyproject.toml`, delete
  `SupabaseDal`, the transport patches, relay key fetch (`fetch_supabase_api_key`), and the
  `STORE_EMAIL/STORE_PASSWORD` env surface (token format can shrink to `{url, api_key, account_id}` —
  keep parsing the old base64 shape for backward compat).
- Rotate/retire the per-account Supabase credentials Holmes used.

### 3.4 Error-semantics parity checklist (easy to regress)

The workers depend on *distinctions*, not just success/failure:
- Reassignment (`mismatch`) → `ConversationReassignedError`, **never retried** (worker exits its claim) — must map to a distinct HTTP code/`code` field (409 REASSIGNED).
- Tool-result rejection (mismatch/not-found) → terminal, log-and-drop, first-result-wins (409 REJECTED).
- Transient infra errors → tenacity retry ×3 with backoff at the client (and the transport-level `RemoteProtocolError` retry).
- `is_realtime_enabled` tri-state → capabilities endpoint must distinguish "definitively off" (2xx false) from "can't reach" (transport error → `None`); the worker only self-disables on the former.
- Best-effort ops (usage events, status/toolset/skill sync, oauth reads) swallow errors; catalog reads return `None`/`[]` on failure — keep identical so startup never breaks on store outage.
- `get_conversation_events` failure vs. empty: a transient failure must raise/retry, not return `[]` (the worker treats `[]` as "no user question" and permanently fails the conversation).

---

## 4. Risks & open questions

1. **RLS → application-layer authorization** (biggest risk). Today the personal-skill and
   conversation scoping is enforced by Postgres policies; the backend must reimplement it
   and needs tests proving cross-user/cross-account isolation.
2. **Realtime replacement scale**: one SSE/WS connection per Holmes cluster — fine at
   current fleet size, but the backend needs connection-count metrics and idle timeouts;
   polling fallback caps the blast radius of any push outage.
3. **Latency budget**: `get_ai_credentials` and skill-hierarchy reads sit on the chat hot
   path; existing client caches (23h token, 60s hierarchy) carry over unchanged, and the
   backend can add its own caching.
4. **Frontend & other Supabase consumers** (robusta UI, `record_feedback` RPC, relay) stay
   on Supabase for now — the database is shared during the transition, so schema stays
   frozen until every consumer migrates.
5. **Token/credential design**: reuse the Robusta UI token as bearer vs. minting a
   dedicated store API key (recommended: dedicated key exchanged via relay at startup,
   mirroring today's `fetch_supabase_api_key` flow, so revocation stays per-cluster).
6. **Payload sizes**: evidence blobs (gzip'd) and event batches are large; keep gzip
   (`Content-Encoding`) on both directions and decide whether evidence unzip moves
   server-side.

---

## 5. Effort estimate (rough)

| Workstream | Size |
|---|---|
| Interface extraction + contract tests (holmesgpt) | S–M |
| Backend API (29 ops + auth + SSE) | L |
| RLS → authz audit & tests | M |
| `RestStoreDal` + `RestRealtimeWorker` | M |
| Shadow/canary tooling + rollout | M |
| Cleanup & credential retirement | S |
