# ADR-002: Integrated Host-Samba Time Machine Targets

## Status

Accepted

## Date

2026-08-01

## Context

runvard already installs and manages host Samba, Samba accounts, storage, and
generic shares. It should provide managed Time Machine destinations for up to
20 Macs while preserving manually maintained shares and avoiding a second SMB
service on port 445.

Time Machine must remain controlled by macOS. runvard needs to manage the server
boundary: authenticated SMB targets, capacity, native storage protection,
replication, service health, and setup guidance. The existing direct append to
`/etc/samba/smb.conf` does not provide adequate validation, rollback, lifecycle,
or isolation for this feature.

## Decision

Implement a dedicated Time Machine module on the existing host Samba service:

- Keep target state in an atomic runvard registry without storing passwords.
- Generate a separate runvard-owned Samba include and validate the complete
  configuration with `testparm` before atomic activation.
- Require authenticated, per-share encrypted SMB and `vfs_fruit` Time Machine
  capability without globally changing unrelated share protocol policy.
- Restrict generated shares to loopback, private, link-local and unique-local
  client networks; public SMB exposure is outside the supported boundary.
- Use one share and capacity policy per Mac, authenticated by a person-level
  backup account that may own multiple Mac targets.
- Provide directory fallback plus native ZFS/Btrfs quotas, storage protection
  points, and replication.
- Keep remote replicas passive until explicit failover promotion.
- Use durable host workers for health, protection, and replication jobs.
- Quarantine and unpublish targets when their expected mount identity or target
  path disappears, and expose measured allocation in target health.
- Leave scheduling, client-side encryption, integrity verification, and restore
  to macOS.

The complete accepted design and decision log are documented in
[Time Machine Integration Design](../plans/TIME_MACHINE_INTEGRATION_DESIGN.md).

## Alternatives Considered

### Extend generic Samba shares

Pros:

- Smaller initial API and UI change.
- Reuses the current share form.

Cons:

- Mixes ordinary file sharing with a backup-specific security and data
  lifecycle.
- Does not naturally model Mac ownership, quotas, protection, replication,
  promotion, or safe data deletion.
- Encourages continued direct edits to the main Samba configuration.

Rejected because the reduced initial code does not justify the operational and
security ambiguity.

### Dedicated Samba container or second daemon

Pros:

- Strong configuration and process isolation.
- Reproducible Samba package and module versions.

Cons:

- Host Samba already owns port 445.
- Requires another address, interface, or macvlan setup.
- Complicates host identities, filesystem permissions, discovery, firewall
  rules, and support diagnostics.

Rejected because it adds significant networking and operational complexity for
the intended 20-Mac scale.

### Reuse host Samba and append target blocks to `smb.conf`

Pros:

- Minimal implementation work.

Cons:

- No reliable ownership boundary between runvard and administrator config.
- Hard to render, validate, diff, roll back, and reconcile safely.
- A partial write or invalid block can affect every Samba share.

Rejected in favor of one generated, atomically replaced include.

## Consequences

Positive:

- No second SMB endpoint or conflicting port.
- Existing identities, storage, monitoring, and federation can be reused.
- Manual shares remain outside the generated target file.
- Time Machine receives dedicated shares, hard quotas where possible, and clear
  health state.
- Remote replicas can be managed without pretending they are active Apple
  backup destinations.

Negative:

- runvard becomes responsible for safe host-Samba and Avahi lifecycle changes.
- Samba fruit/AAPL behavior can be affected by unrelated share definitions, so
  whole-configuration preflight is mandatory.
- Directory-backed targets cannot offer a reliable hard quota in the first
  release.
- Server-side storage protection cannot prove Time Machine backup integrity.
- Person-level accounts do not isolate one Mac from another Mac belonging to
  the same person.

## Security Boundary

The feature is intended for a trusted LAN or existing VPN. Time Machine shares
require SMB transport encryption, authenticated non-guest access, and a
separate client-side encrypted backup. runvard administrators control storage
and replication but should not be assumed to know the Time Machine encryption
password. Bulk replication credentials are restricted to the required receiver
operations and remote replicas remain non-writable until explicit promotion.
