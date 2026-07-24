# Multi-Server Federation Implementation Plan

## Status

Ready for implementation.

## Scope

Implement the accepted design in
[Multi-Server Federation Design](../MULTI_SERVER_FEDERATION.md) without
changing the host-local behavior of Docker, VMs, files, storage, networking,
services, or terminal sessions.

The first release delivers:

- a federation of up to 20 equal runvard instances;
- one-time pairing through any existing member;
- automatic membership and revocation propagation;
- compact status aggregation;
- a fixed Servers dashboard tile and modal;
- role- and Expert-Mode-preserving SSO into a new tab;
- continued standalone operation when federation is disabled.

## Implementation Principles

- Build in small vertical increments and keep the full test suite green.
- Write failing tests before each behavior change.
- Keep private keys, pairing codes, and SSO tickets out of logs.
- Use `RUNVARD_DATA_DIR` consistently so tests can isolate every instance.
- Keep federation optional and disabled by default.
- Avoid a database or message broker for the first release.
- Treat the current uncommitted `static/index.html` changes as user-owned and
  merge federation UI changes without overwriting them.

## Planned File Layout

```text
modules/
  federation/
    __init__.py
    config.py
    crypto.py
    storage.py
    models.py
    membership.py
    pairing.py
    protocol.py
    client.py
    status.py
    sso.py
    service.py
tests/
  test_federation_crypto.py
  test_federation_storage.py
  test_federation_membership.py
  test_federation_pairing.py
  test_federation_protocol.py
  test_federation_status.py
  test_federation_sso.py
  test_federation_api.py
  integration/
    test_federation_mesh.py
scripts/
  verify-federation.sh
```

`server.py` remains the HTTP composition root. Cryptography, storage, polling,
and domain behavior stay in the federation package so route handlers remain
thin.

## API Contract

### Local administrator API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/federation/v1/admin/overview` | Local identity, settings, nodes, aggregate state |
| POST | `/api/federation/v1/admin/enable` | Create identity and enable federation |
| POST | `/api/federation/v1/admin/settings` | Set display name, internal URL, browser URL, allowed CIDRs |
| POST | `/api/federation/v1/admin/pairing-code` | Create a ten-minute single-use code |
| POST | `/api/federation/v1/admin/join` | Join through an existing node and code |
| POST | `/api/federation/v1/admin/nodes/update` | Change trusted peer metadata |
| POST | `/api/federation/v1/admin/nodes/revoke` | Publish a revocation |
| POST | `/api/federation/v1/admin/refresh` | Trigger immediate status and event synchronization |

All routes above require an administrator session. Enabling, joining, changing
addresses, and revoking require the existing dangerous-action confirmation
mechanism.

### Peer API

| Method | Path | Authentication |
| --- | --- | --- |
| POST | `/api/federation/v1/peer/pair` | Valid one-time pairing code |
| POST | `/api/federation/v1/peer/sync` | Signed node request |
| GET | `/api/federation/v1/peer/status` | Signed node request |
| POST | `/api/federation/v1/peer/sso/redeem` | Signed node request |

### Browser handoff API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/federation/v1/sso/start` | Return a no-store auto-submit bridge page in a new tab |
| POST | `/api/federation/v1/sso/accept` | Validate and redeem the ticket, set local cookie, redirect to `/` |

The start endpoint returns HTML, not a JSON ticket response. The bridge page
contains the ticket in a hidden form field, uses a nonce-based Content Security
Policy, sets `Referrer-Policy: no-referrer`, and is never cached.

## Step-by-Step Delivery

### Step 0: Protect the baseline

Tasks:

1. Record the existing dirty worktree and do not modify unrelated files.
2. Run the current test suite and record existing failures separately.
3. Confirm the current login, Expert Mode, dashboard, terminal, Docker, and VM
   behavior on one local test instance.
4. Add a test helper that creates an authenticated FastAPI `TestClient`
   without duplicating token setup across federation tests.

Verification:

```bash
pytest -q
```

Exit gate:

- Existing failures are understood.
- No federation file exists outside documentation.

### Step 1: Make the data directory testable

Problem:

`server.py` currently derives `data/` from its source directory while several
modules use `/opt/runvard/data` or `RUNVARD_DATA_DIR`. Multi-instance tests
need one isolated directory per process.

Tasks:

1. Change `server.py` to honor `RUNVARD_DATA_DIR`, falling back to its current
   source-relative data directory for development.
2. Keep production behavior at `/opt/runvard/data` through the service
   environment.
3. Add regression tests for secret and auth configuration paths.
4. Do not broadly refactor unrelated legacy hard-coded paths in this step.

Files:

- `server.py`
- `tests/test_server_data_dir.py`
- `scripts/install-full.sh`

Exit gate:

- Two processes can use different data directories without sharing session
  secrets or auth configuration.

### Step 2: Add cryptographic identity and atomic storage

Tests first:

- Ed25519 key generation and reload.
- Stable node ID derived from the public key.
- Private key mode is `0600`.
- Canonical JSON produces stable signatures.
- Altered payloads and wrong keys fail verification.
- Interrupted writes leave the previous valid snapshot readable.

Tasks:

1. Add `PyNaCl==1.6.2` to `requirements.txt`; a matching bundled wheel already
   exists.
2. Implement federation paths and constants in `config.py`.
3. Implement canonical serialization, signing, verification, fingerprints,
   and node IDs in `crypto.py`.
4. Implement atomic JSON/JSONL persistence in `storage.py`.
5. Define typed node, event, snapshot, and ticket structures in `models.py`.
6. Add a lazy `ensure_identity()` operation; importing the module must not
   enable federation automatically.

Files:

- `requirements.txt`
- `modules/federation/config.py`
- `modules/federation/crypto.py`
- `modules/federation/storage.py`
- `modules/federation/models.py`
- `tests/test_federation_crypto.py`
- `tests/test_federation_storage.py`

Exit gate:

- Identity and storage tests pass.
- No secret appears in test output or audit data.

### Step 3: Implement signed peer requests

Tests first:

- Valid request accepted once.
- Body, method, path, sender, or recipient modification rejected.
- Unknown and revoked senders rejected.
- Nonce replay rejected.
- More than 30 seconds of clock skew rejected.
- Redirect responses are never followed.
- Internal URL outside configured CIDRs rejected.

Tasks:

1. Define canonical request headers and payload hashing in `protocol.py`.
2. Add a bounded, persisted nonce cache with expiry pruning.
3. Implement a peer client with connect/read timeouts and redirects disabled.
4. Require literal LAN/VPN IP addresses for internal URLs in version 1.
5. Validate every internal destination against configured IPv4/IPv6 CIDRs.
6. Add reusable FastAPI peer-authentication dependency code.

Suggested signed headers:

```text
X-Runvard-Node
X-Runvard-Target
X-Runvard-Time
X-Runvard-Nonce
X-Runvard-Version
X-Runvard-Signature
```

Files:

- `modules/federation/protocol.py`
- `modules/federation/client.py`
- `tests/test_federation_protocol.py`

Exit gate:

- All negative signature, replay, time, redirect, and CIDR tests pass.

### Step 4: Implement membership events

Tests first:

- First enabled node creates one federation ID and self-membership.
- Join, update, and revoke events validate and apply.
- Duplicate events are idempotent.
- Revocation beats older joins and updates.
- A revoked key cannot rejoin under the old node ID.
- Event ordering converges regardless of delivery order.
- Corrupt current state recovers from the last valid snapshot.

Tasks:

1. Implement the append-only signed event log.
2. Derive the registry from validated events.
3. Persist revocation tombstones.
4. Implement event compaction without discarding active revocations.
5. Implement event-diff and merge operations for gossip synchronization.
6. Audit joins, updates, revocations, and rejected events without payload
   secrets.

Files:

- `modules/federation/membership.py`
- `tests/test_federation_membership.py`

Exit gate:

- Randomized event delivery produces the same registry on all test nodes.

### Step 5: Implement one-time pairing

Tests first:

- Codes contain at least 128 bits of entropy.
- Only the code hash is persisted.
- Code expires after ten minutes.
- Code succeeds once and cannot be replayed.
- Repeated wrong codes trigger rate limiting.
- Pairing imports the federation ID, registry, events, and revocations.
- Pairing through B allows C to become known to A after synchronization.

Tasks:

1. Implement code issuance, hashing, expiry, consumption, and pruning.
2. Implement the unauthenticated but code-protected `/peer/pair` handshake.
3. Require proof of possession of the joining private key.
4. Return signed existing-node metadata and a validated membership snapshot.
5. Create and gossip `node_joined`.
6. Display fingerprints and pairing completion details to the administrator.

Files:

- `modules/federation/pairing.py`
- `server.py`
- `tests/test_federation_pairing.py`
- `tests/test_federation_api.py`

Exit gate:

- Three-node transitive admission passes without manually pairing every pair.

### Step 6: Build local status snapshots

Tests first:

- Snapshot tolerates absent Docker or libvirt.
- CPU, RAM, storage, Docker, VM, update, and alert summaries have bounded
  schemas.
- Slow update checks are not executed by a peer status request.
- One failed subsystem does not fail the whole snapshot.
- No filesystem paths, command output, or secrets leak into the summary.

Tasks:

1. Build a fast snapshot every 15 seconds.
2. Use `system.get_stats()` and root disk usage for host metrics.
3. Summarize `docker_mgr.list_containers()` with error isolation.
4. Summarize `vms.list_vms()` with error isolation.
5. Add public cached-summary helpers for application updates and alerts.
6. Add a separate slow update-count cache refreshed no more than every
   30 minutes; never run `apt-get update` in the 15-second snapshot loop.
7. Expose only the cached snapshot from `/peer/status`.

Files:

- `modules/federation/status.py`
- `modules/system_mgr.py`
- `modules/apps.py`
- `modules/monitoring.py`
- `tests/test_federation_status.py`

Exit gate:

- Snapshot generation returns promptly even when Docker, libvirt, or update
  checks fail.

### Step 7: Add polling, health classification, and synchronization

Tests first:

- One failed poll becomes degraded.
- Three failures or 45 seconds become offline.
- Recovery returns to online on the next success.
- Unsupported API becomes incompatible.
- Maximum peer concurrency is four.
- Poll jitter is applied.
- Stopping the service cleanly stops background workers in tests.

Tasks:

1. Implement one idempotent federation background service.
2. Poll up to four peers concurrently.
3. Cache last success, last attempt, failure count, and last snapshot.
4. Exchange missing membership events during the polling cycle.
5. Keep stale snapshots for offline display.
6. Add an immediate refresh operation with rate limiting.
7. Start the worker only when federation is enabled.

Files:

- `modules/federation/service.py`
- `modules/federation/__init__.py`
- `server.py`
- `tests/test_federation_status.py`

Exit gate:

- A stopped peer never blocks local endpoints or other peer polling.

### Step 8: Add administrator and peer routes

Tests first:

- Readonly users can view node status but cannot change federation state.
- Only admins can enable, pair, edit, or revoke.
- Sensitive changes require a matching dangerous-action token.
- Peer routes reject normal browser sessions without node signatures.
- Federation-disabled installations return an empty, stable overview.

Tasks:

1. Import the federation package from `server.py`.
2. Add thin handlers for the API contract above.
3. Extend `_danger_confirm_meta()` for enable, join, address change, and
   revocation.
4. Add structured error codes for clock skew, incompatibility, offline state,
   invalid pairing, and revoked peers.
5. Add audit targets using node IDs rather than secrets or raw tickets.

Files:

- `server.py`
- `modules/federation/__init__.py`
- `tests/test_federation_api.py`
- `tests/test_danger_confirm.py`

Exit gate:

- API authorization matrix and dangerous-action tests pass.

### Step 9: Implement single-use SSO handoff

Tests first:

- Local admin becomes admin on the target.
- Local readonly becomes readonly on the target.
- Expert Mode transfers only for an admin.
- Wrong audience, issuer, signature, or federation rejected.
- Expired and already redeemed tickets rejected.
- Source outage during redemption fails closed.
- Target creates a local cookie signed by its own secret.
- Ticket never appears in redirect URLs, audit records, or access-log paths.

Tasks:

1. Store pending ticket IDs and hashes atomically with a 60-second expiry.
2. Generate the signed assertion only for online, compatible targets.
3. Return a no-store HTML bridge page from `/sso/start`.
4. Apply a strict CSP with a per-response script nonce and target-limited
   `form-action`.
5. Validate and server-redeem the ticket in `/sso/accept`.
6. Create the destination session through the existing `make_token()` path.
7. Preserve the existing session payload formats for backward compatibility.
8. Show a safe fallback page with normal login when redemption fails.

Files:

- `modules/federation/sso.py`
- `server.py`
- `tests/test_federation_sso.py`
- `tests/test_federation_api.py`

Exit gate:

- A ticket succeeds exactly once and produces a fully functional target
  session with the correct role.

### Step 10: Add the Servers tile and modal

Tests/manual checks first:

- Tile hidden or inactive when federation is not enabled, according to the
  final UI decision.
- Badge shows online/total with green, yellow, and red states.
- Current node is labeled and cannot open itself.
- Offline and incompatible peers are disabled.
- Stale values remain visible and clearly marked.
- Online peer opens through the local SSO start form in a new tab.

Tasks:

1. Add `servers` to the fixed `TILES` model in `static/index.html`.
2. Add the server badge to the existing badge refresh path.
3. Add `renderServers()` to the modal dispatcher.
4. Render compact CPU/RAM/disk, Docker, VM, updates, alerts, version, and
   last-contact values.
5. Add administrator controls for enabling federation, generating a code,
   joining, editing node URLs, refreshing, and revoking.
6. Submit the SSO start request through a same-origin form with
   `target="_blank"`.
7. Add responsive styling and keyboard/focus behavior.
8. Add translations for English, German, French, Italian, Spanish, and
   Portuguese.
9. Merge carefully with the existing uncommitted `static/index.html` changes.

Files:

- `static/index.html`
- `tests/test_federation_api.py`

Exit gate:

- The modal remains compact on desktop and mobile.
- Keyboard users can open, inspect, switch, and close it.

### Step 11: Multi-instance integration tests

Tasks:

1. Add helpers that launch three uvicorn processes with:
   - distinct ports;
   - distinct `RUNVARD_DATA_DIR` values;
   - distinct admin credentials;
   - loopback CIDRs allowed for tests.
2. Pair A with B.
3. Pair C only with B.
4. Wait until A learns C.
5. Exercise A to C and C to A SSO.
6. Verify readonly propagation separately.
7. Stop C and verify degraded, then offline.
8. Restart C and verify recovery and event reconciliation.
9. Revoke C from A and verify B rejects C.
10. Attempt replay, wrong-audience, skewed-time, and tampered-signature
    requests.

Files:

- `tests/integration/test_federation_mesh.py`
- `scripts/verify-federation.sh`

Exit gate:

- The complete three-node scenario is repeatable from one command.

### Step 12: Browser and reverse-proxy verification

Tasks:

1. Run two or three local instances.
2. Use Playwright to verify tile badge and compact modal content.
3. Verify offline links are disabled.
4. Verify a new tab receives the correct target cookie and dashboard.
5. Verify terminal, Docker exec, btop, and VNC WebSockets after switching.
6. Test distinct browser URLs that simulate reverse-proxy hostnames.
7. Inspect history, network requests, and logs for ticket leakage.
8. Check mobile layout and keyboard navigation.

Exit gate:

- SSO and existing WebSocket features work through browser-facing URLs.

### Step 13: Installer, update, and operations documentation

Tasks:

1. Preserve federation state during `update.sh`; it already preserves `data/`,
   but add a regression check.
2. Add `PyNaCl` to online and bundled-wheel installation verification.
3. Document allowed CIDRs, internal URLs, browser URLs, time synchronization,
   backup, key loss, clone recovery, and revocation.
4. Add reverse-proxy examples that expose browser routes while restricting
   `/api/federation/v1/peer/*`.
5. Document that restoring/cloning a full data directory requires generating a
   new node identity before joining.
6. Add federation diagnostics to support bundles without private material.

Files:

- `INSTALLATION.md`
- `README.md`
- `scripts/install-full.sh`
- `update.sh`
- `docs/MULTI_SERVER_FEDERATION.md`

Exit gate:

- A fresh installation and an updated installation can both enable and retain
  federation safely.

### Step 14: Staged rollout

1. Deploy to two non-critical servers.
2. Pair and run read-only status collection for at least 24 hours.
3. Enable and test admin and readonly SSO in both directions.
4. Revoke and re-pair one test node.
5. Add a third server through the second server and verify transitive
   membership.
6. Simulate one offline node and one version mismatch.
7. Review audit events and peer request volume.
8. Expand gradually toward 20 nodes.

Rollback:

- Disable federation locally without deleting identity or membership state.
- Existing host-local administration remains available.
- Re-enable after correcting configuration.
- If a key may be compromised, revoke it before disabling or restoring the
  node.

## Verification Commands

Run narrow tests after every step and the full suite at each exit gate:

```bash
pytest -q tests/test_federation_crypto.py
pytest -q tests/test_federation_storage.py
pytest -q tests/test_federation_membership.py
pytest -q tests/test_federation_pairing.py
pytest -q tests/test_federation_protocol.py
pytest -q tests/test_federation_status.py
pytest -q tests/test_federation_sso.py
pytest -q tests/test_federation_api.py
pytest -q
scripts/verify-federation.sh
```

## Definition of Done

- All accepted functional and security requirements are implemented.
- Unit, API, three-node integration, and browser tests pass.
- Twenty configured peers do not produce visible dashboard latency.
- Healthy status changes are visible within 20 seconds.
- Peer failures never block local runvard administration.
- Pairing codes and SSO tickets are single-use and expire correctly.
- No password, private key, pairing code, or ticket is present in URLs or logs.
- Admin and readonly roles work correctly after switching.
- Federation-disabled installations retain current behavior.
- Installation, reverse-proxy, backup, recovery, and revocation procedures are
  documented.

## Explicitly Deferred

- Global logout and automatic invalidation of already-issued destination
  sessions after issuer revocation.
- Remote administration of one peer from another peer's modal.
- Automatic LAN discovery.
- Central workload scheduling or migration.
- More than 20 peers or a message-broker transport.
- Hostname-based internal peer URLs; version 1 uses literal LAN/VPN addresses
  to keep CIDR enforcement deterministic.
