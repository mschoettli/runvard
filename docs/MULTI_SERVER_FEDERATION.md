# Multi-Server Federation Design

## Status

Accepted design. Implementation has not started.

## Date

2026-07-24

## Understanding Summary

- runvard will support a peer-to-peer federation of up to 20 equal instances.
- Every instance will expose a fixed Servers dashboard tile.
- The tile opens a compact modal showing every known instance and its health.
- Selecting an online peer opens it in a new browser tab with a seamless,
  role-preserving login handoff.
- A user does not need a separate local account on the destination instance.
- New instances join through a short-lived one-time pairing code and are then
  distributed automatically to all members.
- Node revocations are propagated to every reachable member and delivered to
  temporarily offline members when they reconnect.

## Goals

- Bidirectional switching between any trusted runvard instances.
- Preserve the authenticated username, role, and current Expert Mode state.
- Keep each instance operational when peers are unavailable.
- Support compact, near-real-time status for up to 20 instances.
- Preserve the existing single-instance behavior when federation is disabled.
- Allow compatible runvard versions to coexist with a visible warning.

## Non-Goals

- High availability or failover for runvard itself.
- Migration or scheduling of containers, VMs, or other workloads.
- Shared storage or synchronization of `/opt/runvard/data`.
- A central controller that executes all host administration operations.
- Global logout in the first release.
- Public peer communication without a trusted LAN or VPN.

## Assumptions

- Peer-to-peer communication uses a trusted LAN or VPN.
- A later reverse proxy provides browser-facing HTTPS and WebSocket forwarding.
- Peer endpoints can be excluded from the public reverse-proxy configuration.
- Federation management is restricted to administrators.
- Each peer may assert identities and roles to every other trusted peer.
- Compromise of one fully trusted peer can therefore affect the whole
  federation; fast revocation is part of the required security model.
- Status refreshes approximately every 15 seconds.
- Offline peers remain visible with stale data but cannot be opened.

## Architecture

### Node identity

Enabling federation creates an Ed25519 key pair. The public key identifies the
node; the private key never leaves the host and is stored with mode `0600`.
The stable `node_id` is derived from the public key.

Each node has:

- a stable node ID;
- a display name and hostname;
- an internal API URL used by peers;
- a browser URL used for opening a new tab;
- a runvard version and federation API version;
- an Ed25519 public key;
- a local trust and revocation state.

Internal and browser URLs must remain separate. A reverse-proxy URL may be
appropriate for the browser while peers should continue using the LAN/VPN URL.

### Local federation storage

Federation state is stored below the configured runvard data directory:

```text
federation/
  identity.json
  identity.key
  registry.json
  events.jsonl
  events.snapshot.json
  pairing.json
  used_nonces.json
```

Writes must be atomic: write a temporary file in the same directory, fsync it,
apply permissions, and replace the destination. Secrets must never be written
to the audit log.

### Federation API

The first API namespace is:

```text
/api/federation/v1/admin/*
/api/federation/v1/peer/*
/api/federation/v1/sso/*
```

Admin routes use the existing runvard administrator session. Peer routes
require signed requests. The SSO acceptance route intentionally accepts a
cross-origin form POST but requires a valid signed, single-use handoff ticket.

Each signed peer request covers:

- HTTP method and path;
- canonical query and body hash;
- sender and recipient node IDs;
- issued-at timestamp;
- unique nonce;
- federation API version.

Requests are rejected for unknown or revoked senders, wrong recipients,
invalid signatures, reused nonces, unsupported versions, or more than
30 seconds of clock skew. Peer HTTP clients do not follow redirects.

## Pairing and Membership

### Initial pairing

1. An administrator creates a random pairing code with at least 128 bits of
   entropy.
2. Only a hash of the code is stored.
3. The code expires after ten minutes, is single-use, and is rate limited.
4. The administrator enters the existing node's internal URL and code on the
   new node.
5. Both nodes exchange public keys and signed node metadata.
6. The new node receives the current membership snapshot and revocation set.
7. The admitting node creates a signed `node_joined` event.
8. The event is distributed to reachable peers and later reconciled with
   offline peers.
9. The UI displays both node fingerprints after successful pairing.

### Membership event log

The initial event types are:

- `node_joined`
- `node_updated`
- `node_revoked`

Every event includes a federation ID, event ID, subject node ID, issuer node
ID, timestamp, payload, and signature. Members exchange known event IDs and
send missing events during periodic reconciliation.

A valid revocation has precedence over older join or update events. A revoked
node can rejoin only with a new key and a new pairing operation. This rule
provides deterministic convergence after network partitions or concurrent
changes.

## Seamless Login Handoff

1. The user selects an online peer in the local Servers modal.
2. The browser requests a handoff ticket for the destination node from the
   current node.
3. The source checks the local session and creates a signed assertion
   containing:
   - unique ticket ID;
   - source and destination node IDs;
   - username;
   - role;
   - current Expert Mode state;
   - issue and expiry timestamps.
4. The ticket expires after 60 seconds.
5. The browser submits the ticket to the destination's browser URL through a
   hidden form with `target="_blank"` and HTTP POST.
6. The destination validates trust, signature, recipient, time window, and API
   compatibility.
7. The destination redeems the ticket through the source node's internal API.
8. The source atomically marks the ticket as consumed.
9. The destination creates its own normal runvard session cookie and redirects
   to `/`.

Tickets never appear in a URL, browser history, application log, or audit
payload. Passwords, password hashes, and source cookies never leave the source.
If the source cannot complete redemption, the destination fails closed and
offers its normal login page.

## Status Collection and UI

### Local status snapshots

Every node builds a compact local snapshot. Peer requests return this cached
snapshot instead of invoking expensive diagnostics on demand.

The snapshot contains:

- online timestamp and hostname;
- CPU, RAM, and primary-storage utilization;
- running and total Docker containers;
- running and total VMs;
- available update count;
- active alert count;
- runvard and federation API versions.

Existing caches must be used for package updates, application updates, and
other slow probes.

### Peer polling

- Poll interval: approximately 15 seconds with jitter.
- Maximum concurrent peer requests: four per node.
- Short per-peer connect and response timeouts.
- The browser only calls its local runvard instance.
- The local backend aggregates cached peer states for the UI.

Peer state is classified as:

- `online`: the latest poll succeeded;
- `degraded`: one poll failed;
- `offline`: three consecutive failures or 45 seconds without success;
- `incompatible`: reachable but no compatible federation API version.

### Servers tile and modal

The fixed tile displays an `online/total` badge:

- green when all peers are online;
- yellow when at least one peer is degraded, offline, or incompatible;
- red when no other peer is reachable.

Each compact server entry shows:

- status, name, and hostname;
- CPU, RAM, and storage percentages;
- Docker and VM running/total counts;
- update and alert counts;
- runvard version and last successful contact.

The current node is labeled "This instance." Offline values remain visible but
are dimmed and marked stale. Offline and incompatible peers cannot be opened.

## Security and Failure Handling

- Internal peer URLs are restricted to configured LAN/VPN CIDR ranges.
- Server-to-server redirects are disabled.
- Nonces provide replay protection for signed peer requests.
- Pairing and SSO endpoints are rate limited.
- Node admission, metadata updates, revocation, SSO handoff, invalid
  signatures, and replay attempts are audited without secrets.
- Clock-skew failures produce an actionable time-synchronization message.
- Address changes require a signed `node_updated` event.
- A lost private key requires revocation and re-pairing with a new key.
- Duplicate node IDs on different hosts are blocked to detect cloned
  installations.
- Registry corruption falls back to the last valid atomic snapshot and can be
  repaired from a trusted peer.
- During a network partition, each node remains locally usable. Membership and
  status reconcile after connectivity returns.

## Compatibility

The federation API has its own version independent of the user-facing runvard
version. Peers negotiate a supported API version. Different runvard releases
may remain connected when they share a federation API version. The UI displays
a warning for release differences and blocks switching only when federation
APIs are incompatible.

## Testing and Acceptance

### Unit tests

- identity generation, permissions, and deterministic node IDs;
- request signing, canonicalization, clock skew, and replay rejection;
- pairing expiry, rate limiting, and single-use behavior;
- membership convergence and revocation precedence;
- ticket creation, validation, redemption, expiry, and replay rejection;
- role and Expert Mode preservation;
- state classification and atomic storage recovery.

### Multi-instance integration tests

Run at least three isolated instances with separate data directories and ports:

1. Pair A and B.
2. Pair C only with B.
3. Confirm A learns about C automatically.
4. Switch A to C and C to A.
5. Revoke C from A.
6. Confirm B receives the revocation and rejects C.
7. Stop and restart a node.
8. Confirm missed events and status converge after reconnection.

### Browser tests

Use Playwright to verify the Servers tile, modal, compact status, stale and
offline behavior, disabled links, role-preserving new-tab login, browser URLs
behind a reverse proxy, and existing WebSocket features after switching.

### Acceptance criteria

- Twenty configured peers do not cause visible dashboard latency.
- A healthy status change appears within 20 seconds.
- Peer outages never prevent local runvard administration.
- Tickets, passwords, private keys, and cookies never appear in URLs or logs.
- Manipulated, expired, or replayed requests and tickets are rejected.
- Federation-disabled installations behave exactly like existing
  single-instance installations.

## Decision Log

| Decision | Alternatives | Rationale |
| --- | --- | --- |
| Equal peer-to-peer federation | Primary node; central controller | Bidirectional switching and no central failure point |
| Ed25519 node identities | Shared global secret; password authentication | Per-node revocation, compact signatures, no distributed master secret |
| One-time pairing code | Manual shared key; LAN discovery | Simple administration with bounded exposure |
| Signed membership events | Shared files; central membership database | Offline reconciliation without shared storage |
| Pull-based cached status | Browser-to-peer calls; push message broker | Fits 20 nodes and preserves same-origin browser behavior |
| Signed single-use SSO handoff | Shared cookies; synchronized passwords | Preserves roles without sharing credentials or cookie keys |
| Server-side ticket redemption | Signed ticket alone | Strong single-use enforcement |
| Separate internal and browser URLs | One URL for all traffic | Supports LAN/VPN APIs and later reverse-proxy browser access |
| Revocation wins conflicts | Last timestamp wins | Deterministic, security-first convergence |
| No shared `/opt/runvard/data` | NFS or replicated data directory | Prevents host-state mixing and concurrent JSON writes |

## Known Risks

- A compromised trusted node can assert identities to other nodes until it is
  revoked.
- Decentralized admission means every trusted administrator can extend the
  federation.
- Reliable signatures and handoffs require synchronized system clocks.
- Long-offline nodes may temporarily show stale membership until they receive
  the current revocation set.
- Publicly exposing peer endpoints increases attack surface even though
  requests are signed; reverse proxies should keep them internal.

## Related Decision

See [ADR-001](decisions/ADR-001-decentralized-multi-server-federation.md).

## Implementation Plan

See
[Multi-Server Federation Implementation Plan](plans/MULTI_SERVER_FEDERATION_IMPLEMENTATION_PLAN.md).
