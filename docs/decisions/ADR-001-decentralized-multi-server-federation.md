# ADR-001: Decentralized Multi-Server Federation

## Status

Accepted

## Date

2026-07-24

## Context

runvard currently manages the host on which it runs. Users want to connect up
to 20 instances, view compact status information, and switch to any online
instance in a new browser tab without logging in again. Instances must remain
equal, work in both directions, preserve user roles, and continue operating
when peers are unavailable.

The existing architecture uses local system calls, local Docker and libvirt
connections, local JSON state, and host-specific sessions. Sharing the runvard
data directory or load balancing existing instances would mix host state and
could execute actions on the wrong server.

## Decision

Implement a decentralized peer federation:

- Each node has an Ed25519 identity.
- Nodes pair through a short-lived, high-entropy one-time code.
- Signed membership events distribute joins, updates, and revocations.
- Nodes poll compact cached status snapshots from their peers.
- A signed, single-use, server-redeemed ticket transfers username, role, and
  Expert Mode state to a selected destination.
- Local host management and data remain local to each node.
- Internal peer and browser-facing URLs are configured separately.

The complete accepted design is documented in
[Multi-Server Federation Design](../MULTI_SERVER_FEDERATION.md).

The executable delivery sequence is documented in
[Multi-Server Federation Implementation Plan](../plans/MULTI_SERVER_FEDERATION_IMPLEMENTATION_PLAN.md).

## Alternatives Considered

### Central controller

Pros:

- Simple central membership and revocation.
- Natural place for shared identities and aggregated status.

Cons:

- Creates a new service and central failure point.
- Conflicts with the requirement that every instance be equal.
- Requires additional deployment and recovery procedures.

Rejected because the expected scale does not justify the operational
complexity and the requested topology is peer-to-peer.

### Browser-to-peer communication

Pros:

- Less server-side aggregation.

Cons:

- Requires every internal address to be reachable from every browser.
- Adds CORS, cookie, certificate, and cross-origin security complexity.
- Makes later reverse-proxy routing harder.

Rejected because the browser should communicate only with its current
same-origin runvard instance.

### Shared data directory and session keys

Pros:

- Appears to offer shared users and sessions with little new API code.

Cons:

- Host-specific Docker, application, and dashboard data would be mixed.
- JSON files are not safe for concurrent multi-host writes.
- Requests could execute against a different physical host.
- Shared cookie keys increase the blast radius of key compromise.

Rejected as unsafe and incompatible with runvard's host-local architecture.

### Manual shared federation secret

Pros:

- Simple initial implementation.

Cons:

- One compromise requires rotating every node.
- No clean per-node identity or revocation.
- Weak audit attribution.

Rejected in favor of per-node asymmetric identities.

## Consequences

Positive:

- No central controller or mandatory new infrastructure.
- Existing single-node administration remains local and independent.
- Nodes can be added through any trusted member.
- Individual keys and nodes can be revoked.
- Browser SSO does not distribute passwords or cookie-signing secrets.

Negative:

- A compromised trusted node can assert identities across the federation until
  revocation converges.
- Membership convergence and conflict handling require careful testing.
- Clock synchronization is required.
- The application gains a versioned internal API and cryptographic state that
  must be maintained across updates and backups.

## Security Boundary

Federation peers are mutually trusted identity issuers. The intended transport
is a private LAN or VPN. A later reverse proxy may expose browser routes, but
peer routes should remain internal. Cryptographic request authentication is
required even on the trusted network.
