# External Server Status Connectors

## Status

Implemented.

## Date

2026-07-26

## Understanding Summary

- The existing trusted connection between runvard instances remains unchanged.
- Linux, Windows Server, Proxmox, and other systems can additionally appear as
  external status tiles.
- External servers are not members of the runvard server group and receive no
  federation trust, SSO tickets, or administrative rights.
- Selecting an external tile opens its configured administration URL in a new
  browser tab. Automatic sign-in remains exclusive to connected runvard
  instances.
- runvard collects CPU, RAM, network, update, and availability data through
  native read-only interfaces wherever possible.
- A monitoring agent is used only as a future fallback when a system has no
  suitable native interface.
- runvard and external servers share one compact, sortable dashboard list.

## Goals

- Display useful status data for heterogeneous servers in the existing
  connected-server dashboard area.
- Avoid installing an agent when a safe native API or management protocol is
  available.
- Keep all credentials and remote communication in the local runvard backend.
- Normalize connector-specific data into one stable status schema.
- Isolate connector failures so one unavailable server cannot delay or break
  the dashboard.
- Preserve the current responsive layout, ordering, and runvard SSO behavior.

## Non-Goals

- Installing the complete runvard control panel on unsupported operating
  systems.
- Providing runvard administration actions for external servers.
- Automatic sign-in to Proxmox, Windows, Linux tools, or arbitrary external
  administration pages.
- Allowing users to enter arbitrary remote shell or PowerShell commands.
- Replacing a dedicated monitoring platform such as Prometheus or Zabbix.
- Making external servers trusted runvard federation members.

## Assumptions

- A runvard installation manages approximately 20 visible servers.
- Status polling occurs approximately every 15 seconds.
- No more than four external status requests run concurrently.
- Servers are reachable through a private LAN or VPN.
- Browser-facing administration links may later use a secured reverse proxy.
- External credentials have the minimum read-only privileges required.
- Update checks may use a slower, separate cache of approximately 15 minutes.
- Optional live integration tests require administrator-provided test systems;
  the default test suite uses realistic simulated responses.

## Architecture

External servers use a separate registry and trust domain from the existing
runvard federation.

```text
Dashboard
    |
    v
Local runvard API ----> Normalized status cache
                            ^
                            |
                      External poller
                       /    |    \
              Proxmox API  SSH  WinRM/HTTPS
                                  \
                               HTTP check
```

The browser calls only its local runvard instance. A background poller selects
the connector configured for each server, applies short timeouts, normalizes
the result, and updates the cache. The dashboard overview combines cached
external statuses with the existing runvard federation overview.

Each external server record contains:

- stable external server ID;
- display name and server type;
- internal status endpoint or host;
- browser-facing administration URL;
- connector type and non-secret options;
- separately stored encrypted credentials;
- TLS verification setting;
- enabled state;
- last successful contact and failure count.

External records must never be inserted into the federation membership event
log or federation key registry.

## Normalized Status

All connectors return the same bounded schema:

```json
{
  "health": "online",
  "cpu_percent": 18.4,
  "ram_percent": 46.2,
  "network_down_rate": 245000,
  "network_up_rate": 32000,
  "updates": 7,
  "captured_at": 1785000000
}
```

Optional measurements use `null` when unavailable. A missing measurement must
never be represented as zero because that would imply a successful reading.
Connector-specific responses and secrets are not exposed through the dashboard
API.

Health states follow the existing dashboard model:

- `online`: latest poll succeeded;
- `degraded`: one or two consecutive polls failed while cached data remains;
- `offline`: three consecutive polls failed;
- `unknown`: no successful poll has completed yet.

The last successful values may remain visible while degraded or offline, with
their capture time retained.

## Connector Model

Every connector implements a small internal interface:

- validate configuration;
- test connectivity and report available measurements;
- collect fast status;
- collect the separately cached update count;
- sanitize errors for administrator display.

Connector implementations are isolated from one another and must not accept
arbitrary user-authored commands.

### runvard

Connected runvard instances continue to use the existing signed peer protocol,
status snapshot, and single-use SSO handoff. They are merged into the dashboard
view but are not copied into the external registry.

### Proxmox

- Use the native Proxmox HTTPS API.
- Prefer a scoped API token with the `PVEAuditor` role.
- Read node availability, CPU, RAM, network, and update data.
- Verify TLS by default.
- Keep the administration URL independent from the internal API endpoint.

### Linux

- Use SSH public-key authentication.
- Collect status through fixed read-only commands and `/proc` data.
- Detect supported package managers explicitly.
- Support update counts for `apt`, `dnf`, `yum`, `zypper`, `apk`, and
  `pacman` through fixed connector code.
- Return `null` for a metric when the target system cannot provide it.

### Windows Server

- Use WinRM over HTTPS.
- Require a restricted account.
- Execute only fixed PowerShell/CIM queries embedded in the connector.
- Collect CPU, memory, network, and Windows Update status.
- Do not enable unencrypted WinRM HTTP as an automatic fallback.

### Generic Link

- Check an HTTP or HTTPS endpoint for availability.
- Allow a separately configured administration URL.
- Report unsupported detailed measurements as `null`.
- Provide a useful tile even when only reachability is available.

## Polling, Caching, and Reliability

- Poll approximately every 15 seconds.
- Run no more than four external connector calls concurrently.
- Use an approximate three-second connection timeout and ten-second total
  status timeout.
- Cache expensive update checks separately for approximately 15 minutes.
- Preserve the last successful snapshot after transient failures.
- Keep manual refresh subject to the same concurrency and timeout limits.
- Stop polling disabled servers without deleting their configuration.

One connector exception must be contained and converted into that server's
health transition. It must not terminate the poller or delay unrelated
connectors.

## Security and Secret Storage

- Use minimum read-only permissions for API tokens and remote accounts.
- Prefer Proxmox API tokens and SSH keys over reusable passwords.
- Store credentials separately from normal server configuration.
- Encrypt secrets at rest with a local machine key protected by restrictive
  filesystem permissions.
- Never return credentials to the browser after creation.
- Never include secrets, SSH material, WinRM passwords, tokens, or raw
  authorization headers in application logs, audit records, status payloads,
  exports, or support bundles.
- Reject unsafe internal endpoints and redirects according to the same
  private-network principles used by federation peer URLs.
- Verify TLS by default; disabling verification requires an explicit
  administrator choice.
- Remove the associated encrypted secret when an external server is deleted.

The server form supports a connection test before saving. It reports success
or a sanitized error category without revealing credentials or raw remote
output.

## API and Administration

The external-server API is separate from federation routes and is restricted to
administrators for mutations. It provides:

- list and overview;
- create;
- update;
- connectivity test;
- manual refresh;
- enable or disable;
- delete.

Readonly users may receive the sanitized dashboard overview but cannot access
connector configuration, internal endpoints, credential metadata, or
diagnostic error details.

The public overview returns only display fields, the administration URL,
normalized status, and the server kind needed by the UI.

## Dashboard Experience

The existing **Connected servers** section remains the single visual home for
both runvard and external servers.

The add button first offers:

- runvard instance;
- Proxmox;
- Linux;
- Windows Server;
- other server or link.

The form then displays only fields relevant to the selected connector.

Every tile keeps the current compact visual structure:

- server name;
- small health dot;
- small new-tab icon;
- CPU, RAM, and network line charts;
- available update count.

Unavailable measurements use a neutral interrupted line rather than a numeric
zero. Clicking a runvard tile starts the existing SSO flow. Clicking an
external tile opens its configured administration URL without attempting
authentication. An external link may still be opened while its status endpoint
is offline.

runvard and external tiles participate in one drag-and-drop order. Desktop and
mobile behavior, including the compact mobile list and **Show more** control,
remain consistent with the existing connected-server component.

The administration modal supports connection testing, editing, manual refresh,
disabling, and confirmed removal.

## Localization

All new user-facing text must be available in:

- German;
- English;
- French;
- Italian;
- Spanish;
- Portuguese.

Connector errors shown to administrators use localized, sanitized categories
such as timeout, authentication failed, certificate invalid, or endpoint
unreachable. Raw command output is not displayed.

## Error Handling

The backend records per server:

- last attempt;
- last success;
- consecutive failure count;
- normalized health;
- sanitized administrator-only error category;
- cached normalized snapshot.

Authentication failures, certificate errors, timeouts, malformed responses,
and unsupported metrics are handled without exposing secrets. Saving does not
require the target to be online, so temporarily unavailable systems can still
be configured and tested later.

## Testing Strategy

### Automated tests

- Connector contract tests with simulated Proxmox, SSH, WinRM, and HTTP
  responses.
- Normalization tests for missing, invalid, and boundary metric values.
- Polling tests for concurrency, timeouts, caching, and health transitions.
- Secret-storage tests proving plaintext credentials do not appear in normal
  storage, APIs, logs, or audit data.
- Authorization tests for admin and readonly users.
- SSRF, redirect, and TLS-validation tests.
- UI tests for mixed runvard and external tiles.
- Drag-and-drop persistence with mixed server types.
- Tests proving SSO is used only for runvard targets.
- Responsive browser tests for desktop and mobile.
- Translation coverage checks for all six supported languages.

### Optional live tests

- Proxmox test host with a scoped API token.
- Linux test host with a restricted SSH key.
- Windows Server test host with WinRM over HTTPS.

Live tests are opt-in and must not be required for the default local or CI test
suite.

## Implementation Sequence

1. Add external server registry and encrypted secret storage.
2. Define the connector interface, normalized status model, cache, and poller.
3. Implement generic HTTP, Proxmox, and Linux connectors.
4. Implement the Windows Server WinRM connector.
5. Add the external-server API and authorization rules.
6. Extend the existing connected-server UI and shared ordering.
7. Add all translations, setup guidance, and minimum-permission documentation.
8. Complete security, backend, browser, and optional live verification.

## Decision Log

| Decision | Alternatives | Reason |
| --- | --- | --- |
| Prefer native, agentless connectors | Mandatory universal agent | Avoid installation and maintenance on every target |
| Keep a future agent only as fallback | No agent support ever | Preserve extensibility for systems without useful native interfaces |
| Separate external servers from federation | Put every server in the runvard federation | Prevent non-runvard systems from gaining trust or SSO authority |
| Use a local poller and normalized cache | Browser polls targets directly | Protect credentials, avoid CORS issues, and isolate failures |
| Use fixed read-only remote operations | Administrator-defined commands | Reduce remote-execution and injection risk |
| Keep SSO only for runvard instances | Attempt automatic login everywhere | External platforms have incompatible authentication and trust models |
| Show missing values as unavailable | Display zero | Avoid presenting fabricated measurements |
| Use one mixed, sortable tile list | Separate dashboard sections | Preserve the compact dashboard model and user-defined ordering |
| Cache update checks longer than live metrics | Check updates every 15 seconds | Update discovery is slower and more expensive |
| Verify TLS by default | Automatically accept private/self-signed endpoints | Fail safely and make exceptions explicit |
