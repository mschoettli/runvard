# Installation

runvard can be installed on a Debian or Ubuntu server with one command.
Run the installer with root privileges on the target server.

```bash
curl -fsSL https://raw.githubusercontent.com/mschoettli/runvard/main/install.sh | sudo bash
```

German installer output:

```bash
curl -fsSL https://raw.githubusercontent.com/mschoettli/runvard/main/install.sh | sudo bash -s -- --lang de
```

The installer downloads the current runvard release when needed, installs required system packages, creates the Python virtual environment, installs Python dependencies, writes the systemd service, and starts runvard.
It asks for the installer language, admin username, admin password, and web port.
The selected installer language is also stored as the initial web interface language.
If the password is left empty, the installer generates a random password and prints it at the end.
The selected username, password, port, and language are stored in `/opt/runvard/data/runvard.env` and loaded by the systemd service.
After installation, runvard is available at the address printed by the installer; the default port is `8080`.

For automated installs, pass options or environment variables:

```bash
curl -fsSL https://raw.githubusercontent.com/mschoettli/runvard/main/install.sh | sudo RUNVARD_PASS='change-me' RUNVARD_LANG=en bash -s -- --yes --port 8080
```

Supported installer options:

- `--lang en|de`: choose English or German installer output
- `--port <n>`: set the web port
- `--user <name>`: set the admin username
- `-y`, `--yes`: install without prompts

Supported installer environment variables include `RUNVARD_LANG`, `RUNVARD_USER`, `RUNVARD_PASS`, `RUNVARD_PORT`, `RUNVARD_YES`, `RUNVARD_ARCHIVE_URL`, `RUNVARD_SOURCE_DIR`, and `RUNVARD_SOURCE_COMMIT`.
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

The updater is English-only. It syncs program files with `rsync`, keeps `/opt/runvard/data` unchanged, refreshes Python dependencies, records the source commit when available, restarts the service, and checks `/login`.
For emergency updates, dependency refresh can be skipped:

```bash
sudo RUNVARD_SKIP_PIP=1 bash update.sh
```

## Reverse Proxy

runvard can run behind Nginx Proxy Manager, OpenResty, Nginx, Caddy, or another reverse proxy.
Keep runvard itself on plain HTTP unless you have a specific reason to terminate TLS in the app.

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
