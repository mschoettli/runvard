# External server setup

runvard can display status cards for Linux, Windows Server, Proxmox, and
generic HTTP(S) targets. These targets are status links, not trusted runvard
peers: selecting a card opens the configured administration URL in a new tab
without automatic sign-in.

## Common requirements

- The status target must use a literal private IPv4 or IPv6 address reachable
  from the runvard server.
- The administration URL may use the later reverse-proxy hostname because it
  is opened only by the browser.
- Use a dedicated account or token with the least possible read-only access.
- Keep TLS verification enabled. If a private certificate authority is used,
  install that CA in the runvard host's trust store.
- Use **Test connection** before saving.

The supported private ranges are `10.0.0.0/8`, `172.16.0.0/12`,
`192.168.0.0/16`, `100.64.0.0/10`, and `fd00::/8`. Redirects are not followed.

## Proxmox

1. Create a dedicated Proxmox user and API token.
2. Assign the token the built-in `PVEAuditor` role for the nodes it may read.
3. Enter the API base address, for example `https://10.0.0.20:8006`.
4. Enter the token ID in Proxmox form, for example
   `monitor@pve!runvard`, and its secret.
5. Optionally enter a node name. If omitted, runvard selects an online node.

The connector reads node status, RRD network data, and the APT update list. It
does not start, stop, or modify guests.

## Linux

1. Create a dedicated unprivileged monitoring user on the target.
2. Add a dedicated public key to that user's `authorized_keys`.
3. Copy the matching private key into the runvard connection form.
4. Determine and verify the target host-key fingerprint:

   ```sh
   ssh-keyscan -p 22 10.0.0.21 | ssh-keygen -lf - -E sha256
   ```

5. Enter only the `SHA256:...` fingerprint shown for the intended host.

runvard executes fixed read-only commands for `/proc` CPU, memory, and network
data. The update check uses a fixed command for a detected supported package
manager. The form cannot supply shell commands.

## Windows Server

1. Configure WinRM with an HTTPS listener and a certificate whose name or IP
   matches the target.
2. Permit the runvard host through the Windows firewall only to the WinRM
   HTTPS port, normally `5986`.
3. Create a dedicated restricted monitoring account.
4. Enter an endpoint such as `https://10.0.0.30:5986/wsman`, the account name,
   and its password.

The connector uses NTLM only inside the TLS connection and runs fixed
PowerShell/CIM status queries. WinRM over unencrypted HTTP is rejected. The
account needs enough rights to read CIM, network-adapter statistics, and the
Windows Update count; no arbitrary PowerShell is accepted from the browser.

## Generic HTTP(S) link

Use this option for appliances, routers, NAS systems, or any other server with
a lightweight health endpoint.

1. Enter a private status URL including its port, for example
   `https://10.0.0.50:8443/health`.
2. Enter the administration URL that the card should open.

A successful HTTP response marks the target online. CPU, RAM, network, and
update values remain unavailable because the generic check does not parse a
vendor-specific response.

## Data and failure behavior

Fast status is polled approximately every 15 seconds with at most four
external checks running concurrently. Update counts are cached for about
15 minutes. One or two failed checks mark a target degraded; the third marks
it offline. The last successful measurements remain on the card.

Configuration and encrypted credentials are stored below the configured
runvard data directory in `external-servers/`. Removing a server also removes
its encrypted secret.
