# Development

runvard manages privileged host resources. Keep local checks split from
target-host verification:

## Dependency-light checks

These checks run without FastAPI, Docker, systemd, libvirt, or root access.
They validate syntax, static API contracts, runtime path configuration, and
the safest parts of the host-operation input validation.

```bash
python3 -m unittest discover -s tests
python3 -m compileall server.py modules tests
```

The Codex bundled Python runtime can run the same tests:

```bash
/Users/mschoettli/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -m unittest discover -s tests
```

## Runtime data directory

Persistent data defaults to `/opt/runvard/data`. For local tests or isolated
manual runs, override it:

```bash
RUNVARD_DATA_DIR=/tmp/runvard-data python3 -m unittest discover -s tests
```

## Full app verification

The full web app requires the runtime dependencies from `requirements.txt`.
Most host-management features also require a Debian or Ubuntu target host with
root privileges and the relevant system packages installed, such as Docker,
systemd, libvirt, Samba, NFS, LVM, ZFS, or Btrfs.

Create an isolated environment for full FastAPI checks:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
RUNVARD_DATA_DIR=/tmp/runvard-test-data .venv/bin/python -m unittest discover -s tests
RUNVARD_DATA_DIR=/tmp/runvard-test-data .venv/bin/python -c "import server; print(len(server.app.routes))"
```

Use `INSTALLATION.md` for target-host setup.

## Safety verification checklist

Before changing host-management routes, keep these checks green:

```bash
scripts/verify-local.sh
```

The script expands to the same local gates:

```bash
python3 -m unittest discover -s tests
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall server.py modules tests
test -s README.md INSTALLATION.md DEVELOPMENT.md requirements.txt runvard.service modules/runtime.py static/btop.html tests/test_static_contracts.py tests/test_app_runtime.py scripts/verify-local.sh scripts/verify-target-host.sh scripts/verify-api-only.sh
bash -n install.sh uninstall.sh update.sh scripts/install-full.sh scripts/verify-local.sh scripts/verify-target-host.sh scripts/verify-api-only.sh
git diff --check
node -e "const fs=require('fs'); const html=fs.readFileSync('static/index.html','utf8'); let i=0; for (const m of html.matchAll(/<script[^>]*>([\\s\\S]*?)<\\/script>/g)) { new Function(m[1]); i++; } console.log('checked script blocks', i);"
```

It also runs with an isolated `PYTHONPYCACHEPREFIX`, fails if generated
`__pycache__`, `.pyc`, or `.pyo` files appear in the workspace outside ignored
development directories, and checks local Markdown links.

Local HTTP smoke checks should verify login, JSON error responses, runtime
configuration, and that protected destructive routes reject missing or
mismatched confirmation tokens. This includes reboot/shutdown, disk formatting,
partition table changes, partition creation, mounts/unmounts, swap changes,
RAID/LVM changes, LUKS format/open/close, filesystem growth, ZFS/Btrfs
create/scrub/destroy operations, iSCSI login/logout, permanent storage deletes,
Docker container create/update/start/stop/restart/remove actions, Docker image
pull/remove actions, Docker volume deletes, Compose save/start/stop/restart/
delete actions, VM create/start/shutdown/reboot/clone/CD-ROM/hardware/snapshot/
pool/volume actions, firewall rule additions/removals, bond/bridge/VLAN
creation, IP reconfiguration, interface deletion, package installs/removals,
package upgrades, runvard self-updates, app install/update/uninstall actions,
service actions, backup job creation/runs, Samba/NFS share changes, external
SMB/NFS mounts, file write/rename/copy/move/mkdir/delete/upload/zip/unzip/trash/
share-link actions, runvard account/login changes, OS user/group/password/
SSH-key changes, certificate generation, monitoring alert changes, Cron job
creation, hostname changes, AppArmor profile changes, unattended-upgrades
policy changes, tuned profile changes, kdump actions, and sosreport generation.
Do not run valid reboot, shutdown, format, delete, update, install, service,
policy, share, identity, account, backup, schedule, diagnostics, file mutation,
storage mutation, container/Compose mutation, VM mutation, or network
reconfiguration calls on a workstation.
Readonly accounts must also be unable to issue confirmation tokens or perform
token-protected destructive actions.

The remaining full verification must happen on a disposable Debian or Ubuntu
target host. Cover Docker daemon access, Compose project lifecycle, systemd
service actions, package updates, storage tools, libvirt VM actions, Samba/NFS
shares, and network configuration changes there.

Use the target-host verifier after installing runvard on that disposable host:

```bash
sudo /opt/runvard/scripts/verify-target-host.sh
```

By default the script is non-destructive. It checks service health, login,
JSON error responses, confirmation-token enforcement, Docker/Compose/libvirt
availability, and read-only discovery APIs. Any HTTP 5xx response from those
discovery APIs is treated as a failure. It also checks structured discovery
response fields such as `containers`, `services`, `interfaces`, `users`, and
`vms`, plus parameterized read API validation for host-command-backed endpoints
such as service logs.

The verifier must authenticate with an admin account. It checks
`/api/auth/status` after login and stops early if the configured
`RUNVARD_USER`/`RUNVARD_PASS` belongs to a readonly account, because
confirmation-token and mutation-boundary checks require admin privileges.

The non-destructive target verifier also issues valid confirmation tokens for
intentionally invalid mutation requests and expects those requests to fail with
HTTP 400 before host tools run. These API-boundary checks cover unsafe backup
rsync sources, unsafe NFS export options, invalid upload filenames, directory
share-link attempts, empty file job path-list entries, invalid account roles,
invalid package names, filesystem types, VM volume formats, file job actions,
AppArmor modes, and kdump actions.

For a running local or staging web process that is not installed as the
`runvard` systemd service, the same HTTP/API contract checks can run without
systemd and host-integration checks:

```bash
RUNVARD_API_ONLY=1 RUNVARD_URL=http://127.0.0.1:8080 RUNVARD_USER=admin RUNVARD_PASS=runvard scripts/verify-target-host.sh
```

For local development, use the wrapper to start a temporary API process and run
the same API-only verifier in one step:

```bash
scripts/verify-api-only.sh
```

The wrapper uses `.venv/bin/python` when available, falls back to `python3`,
starts uvicorn on `127.0.0.1:8876`, stores runtime data and Python bytecode in a
temporary directory, waits for `/login`, runs `scripts/verify-target-host.sh` in
`RUNVARD_API_ONLY=1` mode, and stops the server afterward.

This API-only mode is useful while developing, but it does not prove service
installation, journal health, Docker daemon access, libvirt access, or other
target-host integrations and does not replace the normal verifier on a
disposable Debian or Ubuntu host.

To run valid mutating checks, use a throwaway host and opt in explicitly:

```bash
sudo RUNVARD_DESTRUCTIVE=1 RUNVARD_TEST_SERVICE=cron.service /opt/runvard/scripts/verify-target-host.sh
```

The static contract tests also guard the frontend wiring for confirmation-token
protected routes. If a duplicate JavaScript function definition reintroduces a
plain `post(...)` call to a protected destructive route, the dependency-light
test suite should fail before the change reaches a target host. They also force
every FastAPI `POST` route to be classified as either confirmation-token
protected or intentionally allowed without confirmation, so new mutation routes
cannot quietly bypass the safety contract. Compose validation must keep generic
user-supplied Compose files from mounting sensitive host paths such as the
Docker socket, while built-in catalog apps that intentionally need the Docker
socket must declare that privilege and validate with the catalog contract test.
File and storage contracts also cover recursive directory operations with
symlinks, NFS remote export validation, and root-device/root-pool guards for
LVM/ZFS destructive actions.
