# Installation

runvard is installed from an immutable, attested release. Install the GitHub CLI
(`gh`) first, choose an explicit release, download its bootstrap without root
privileges, and verify its GitHub/Sigstore provenance before execution:

```bash
VERSION=vX.Y.Z
gh release download "$VERSION" --repo mschoettli/runvard --pattern install.sh
gh attestation verify install.sh --repo mschoettli/runvard
sudo bash install.sh --version "$VERSION"
```

Never pipe an unversioned network response into `sudo bash`. The bootstrap downloads
the complete release archive to a private temporary directory, verifies its SHA-256
checksum and GitHub artifact attestation, validates every archive path and symlink,
and only then extracts and executes it. Any missing or failed verification aborts.

The installer downloads the current runvard release when needed, installs required system packages, creates the Python virtual environment, installs Python dependencies, writes the systemd service, and starts runvard.
It asks for the installer language, admin username, admin password, and web port.
The selected installer language is also stored as the initial web interface language.
If the password is left empty, the installer generates a cryptographically random
password and writes it only to the controlling terminal. Passwords are stored as
salted hashes in the mode-0600 account database; `runvard.env` contains no password.
After installation, runvard is available at the address printed by the installer; the default port is `8080`.

For automated installs, pass options or environment variables:

```bash
sudo RUNVARD_PASS='change-me' RUNVARD_LANG=en bash install.sh --version vX.Y.Z --yes --port 8080
```

Supported installer options:

- `--lang en|de`: choose English or German installer output
- `--port <n>`: set the web port
- `--user <name>`: set the admin username
- `-y`, `--yes`: install without prompts

Supported installer environment variables include `RUNVARD_LANG`, `RUNVARD_USER`,
`RUNVARD_PASS`, `RUNVARD_PORT`, and `RUNVARD_YES`. The two credential variables
are migration/bootstrap inputs only and must be supplied together and non-empty.
`RUNVARD_LANG=en` starts the installer and web interface in English; `RUNVARD_LANG=de` starts them in German. Users can still change the interface language later from the web UI.

## Bundled Wheels

If the bundled `wheels/` directory is present, the installer uses those local wheel files first to speed up Python dependency installation.
The installer still uses the internet when required to update `pip`, install native Python bindings, and fetch system packages that are not available locally.
The bundled wheels are intended for Python 3.13 on x86_64 Linux.

## Service Management

```bash
systemctl status runvard
systemctl restart runvard
journalctl -u runvard -f
systemctl stop runvard
```

## Update

Run the update script from a local runvard release directory.

```bash
cd /opt/runvard
sudo bash update.sh
```

The web UI self-update and the installed bootstrap use the same immutable release,
checksum, attestation, and archive-structure verification. They preserve
`/opt/runvard/data`, record the verified source commit, restart the service, and
check `/login`. A remote branch install is available only through the explicit
`--developer-branch NAME` option and prints a production-safety warning.
For emergency updates, dependency refresh can be skipped:

```bash
sudo RUNVARD_SKIP_PIP=1 bash update.sh
```

## Reverse Proxy

runvard can run behind Nginx Proxy Manager, OpenResty, Nginx, Caddy, or another reverse proxy.
Keep runvard itself on plain HTTP unless you have a specific reason to terminate TLS in the app.
Terminate TLS at the proxy and prevent direct untrusted access to the upstream port.
Runvard does not trust `Forwarded` or `X-Forwarded-*` headers for client identity.
Session cookies remain `HttpOnly` and `SameSite=Strict`; HTTPS requests receive the
`Secure` cookie flag.
Set `RUNVARD_TRUSTED_PROXIES` to a comma-separated list of the proxy IP addresses or
CIDR networks only when the upstream is reachable exclusively through those trusted
proxies. Only then is `X-Forwarded-Proto: https` used for the cookie security flag;
forwarded client-address headers are never used as authentication evidence.

## Root terminal security

The web terminal is a root shell. Opening it requires an administrator to re-enter
the current password. The resulting authorization is short-lived, bound to that
account, and single-use. Sessions use random isolated tmux names, are limited to one
per administrator, and are terminated on disconnect, logout, idle timeout, maximum
duration, or service shutdown. Runvard must therefore remain restricted to a VPN,
trusted management network, or hardened HTTPS reverse proxy. Moving privileged
operations into a separate least-privilege helper remains a future hardening topic.

## Release signing and recovery

`.github/workflows/release.yml` builds release assets from a version tag and creates
GitHub Artifact Attestations using short-lived OIDC credentials. No long-lived
private signing key is stored in the repository. GitHub's Sigstore trust root and
the fixed `mschoettli/runvard` repository identity are the trust anchor. If the
repository or release workflow is compromised, stop publishing, revoke affected
releases, repair and review the workflow, and publish a new version; installers
never fall back to unattested artifacts.

For Nginx Proxy Manager running as a Docker container on the same host, create the proxy host with these values:

- Scheme: `http`
- Forward Hostname / IP: `host.docker.internal`
- Forward Port: the configured runvard port, usually `8080`
- Websockets Support: enabled
- Block Common Exploits: enabled

The runvard Nginx Proxy Manager app template includes this Docker host mapping:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

If OpenResty or Nginx Proxy Manager returns a 502 error such as `connect() failed (111: Connection refused) while connecting to upstream`, the proxy is usually pointing at the wrong upstream.
Inside a Docker proxy container, `127.0.0.1` and `localhost` refer to the proxy container, not the host running runvard.
Use `host.docker.internal` with the mapping above, or use the host's LAN IP address and the configured runvard port.

For a plain Nginx or OpenResty server block, include WebSocket upgrade headers for terminal, Docker exec, btop, and VNC sessions:

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

## Uninstall

Run the English-only uninstall script from the runvard directory on the server.

```bash
cd /opt/runvard
sudo bash uninstall.sh
```

The default uninstall stops and removes the runvard service and backs up the data directory before removing `/opt/runvard`.
Use `--purge` to remove runvard and its data without creating a backup. Use `--yes` to skip the confirmation prompt.

```bash
sudo bash uninstall.sh --purge
```

Packages installed through the system package manager and host changes made through runvard, such as shares, users, sudo policy, or cron entries, are not removed automatically.
