# runvard

runvard is currently in active development.

The core features are already working, while several advanced features are still being built, improved, or tested.

runvard is a web-based server control panel for Debian and Ubuntu systems.
It brings monitoring, system administration, Docker, apps, storage, networking,
security, backups, and maintenance into one private interface.

runvard is built for people who operate their own servers, homelabs, and small
infrastructure setups and want direct control without constantly switching between
terminal commands, log files, Docker tools, and separate admin interfaces.

<img width="3726" height="2120" alt="image" src="https://github.com/user-attachments/assets/74697af4-ce6f-4ce7-b215-d23b6dcf414f" />


## What is runvard?

runvard is a central management interface for Linux servers. After installation, it
runs as a local web service on the server and allows administration through a web
browser.

The application is intentionally designed for private and trusted environments:
homelabs, home servers, small teams, local networks, or servers placed behind a
properly secured reverse proxy.

## What does runvard do?

runvard combines many common server administration tasks in one interface:

- Monitor server health in real time
- Manage system services
- Control Docker containers and Docker Compose projects
- Install and manage apps
- Work with files and shares directly in the browser using a secure path picker
- Administer storage, disks, and filesystems
- Configure networking, firewall rules, and interfaces
- Manage users, groups, SSH keys, and permissions
- Schedule and run backups
- Create and manage Time Machine backups for Macs
- Inspect logs, alerts, and audit entries
- Apply system updates and maintenance tasks
- Manage virtual machines
- Connect up to 20 runvard servers and switch between them in both directions
- Add Linux, Windows Server, Proxmox, and generic server status links
- Organize services and links on a customizable dashboard

## Features

### Dashboard

The dashboard is the main starting point in runvard. It shows important services,
installed apps, Compose projects, and custom links as tiles. Ordering and visibility
can be adjusted so frequently used services stay easy to reach.

runvard offers an Original and a Modern interface theme. The selected theme also
applies to the sign-in page, is remembered in the browser, and can be changed
before or after signing in. Both variants are responsive and keyboard accessible.

### Multi-server federation

The Servers tile can connect up to 20 equal runvard instances over a private
LAN or VPN. Pairing uses a ten-minute, single-use code. Every server remains
independent, membership is synchronized automatically, and switching works in
both directions.

The compact server list shows availability plus CPU, RAM, disk, Docker, VM,
update, alert, and version summaries. Opening an online server creates a
short-lived, single-use sign-in handoff in a new tab while preserving the
current admin or read-only role and Expert Mode. Internal peer URLs must use
literal private IP addresses; separate browser URLs may later point at secured
reverse-proxy hostnames.

### External server status

Linux, Windows Server, Proxmox, and other HTTP-accessible systems can be added
to the same connected-server area without becoming trusted runvard peers.
Their compact cards show availability and, where supported, CPU, RAM, network,
and pending updates. Selecting a card opens its configured administration page
in a new tab; automatic sign-in is provided only between trusted runvard
instances.

External status collection uses server-side, read-only connectors: SSH with a
pinned host key for Linux, the Proxmox HTTPS API, WinRM over HTTPS for Windows
Server, or a simple HTTP(S) availability check. Credentials remain encrypted
on the local runvard server and are never returned to the browser. See
[External server setup](docs/EXTERNAL_SERVER_SETUP.md) for the required target
configuration.

### Languages

runvard supports multiple interface languages. The installer can currently be run
in English or German, and the selected installer language is stored as the initial
web interface language. Users can still switch the interface language later from
the web UI.

The main web interface includes complete language options for English, German,
French, Italian, Spanish, and Portuguese. Translation consistency is checked as
part of the project's automated quality gates.

### System monitoring

runvard displays live system information such as CPU usage, memory, disks, network
activity, temperatures, processes, and historical metrics. This makes it easier to
spot load, bottlenecks, and unusual behavior.

### Docker and apps

Docker containers can be started, stopped, created, and monitored. runvard shows
container logs, runtime information, images, volumes, resource limits, and live CPU
and memory usage. In the Modern theme, Compose services are grouped into compact
app cards with aggregated health and resource information. Docker Compose projects
can also be created, edited, and managed.

runvard also includes an app section for self-hosted services based on Docker
Compose, making common homelab and server applications easier to deploy. App cards
show available updates and keep the current update state visible while an update
is running.

### Files and shares

The built-in file manager provides browser-based access to files and folders. Files
and directories can be created, moved, copied, renamed, deleted, uploaded,
downloaded, archived, and extracted.

runvard also supports share and mount workflows, including Samba- and NFS-related
operations.

A shared path picker helps select local folders, files, and block devices for
backups, shares, mounts, Docker volumes, virtual machines, and other workflows.
Normal mode is limited to safe storage locations, while Expert Mode can browse the
full filesystem. Selections are validated for type, access, and write permissions
before they are accepted.

### Storage management

runvard provides tools for disks, partitions, mounts, and filesystems. Depending on
the system, it can work with SMART data, swap, RAID, LVM, LUKS, ZFS, Btrfs, and
iSCSI.

Storage operations can be sensitive host-level actions and should only be performed
on systems whose disk layout is understood.

### Networking and firewall

Network interfaces, firewall rules, and advanced configurations such as bonds,
bridges, and VLANs can be managed through runvard. This makes it useful for servers
with more than a single basic network interface.

### Security and users

runvard includes tools for managing Linux users, groups, passwords, SSH keys, sudo
permissions, and local certificates. Actions can be reviewed through audit views.

### Expert Mode

Expert Mode is an optional session mode for administrators. It reveals advanced
areas and actions that can change disks, services, users, packages, networking,
diagnostics, and other host-level settings.

By default, runvard keeps the interface calmer and hides the most sensitive
controls. An admin can enable Expert Mode from the user menu in the top-right
corner. When it is enabled, additional tabs, buttons, and system actions become
available for the current session.

Expert Mode should only be used when you understand the affected system area.
Some actions can interrupt services, change network access, alter storage devices,
or modify privileged host configuration.

### Services, logs, and maintenance

System services can be listed, started, stopped, and restarted. Logs can be filtered
and inspected. Maintenance tools include package updates, cron jobs, power and
energy options, hostname management, and additional system utilities.

### Backups

Backup jobs can be created with a four-step assistant that guides users through
source, destination, schedule, and review. The assistant validates paths and warns
about same-disk copies and mirror jobs that may delete files at the destination.
Jobs can be scheduled or run manually, and the history shows successful and failed
runs.

### Time Machine backups

runvard can create and manage dedicated Time Machine backup targets for Macs over
encrypted SMB. Each Mac receives its own share and capacity policy. The interface
shows target health, available capacity, protection state, and recent backup
activity, while LAN discovery makes the target available to macOS.

Where supported, runvard uses native ZFS or Btrfs quotas and snapshots to protect
the backup storage. Targets can also be copied to a passive remote replica that is
activated only through an explicit failover. Time Machine scheduling, client-side
encryption, backup integrity, and restore remain controlled by macOS.

### Virtual machines

When virtualization is available on the server, runvard can manage virtual machines.
This includes starting and stopping VMs, snapshots, storage pools, virtual disks,
network interfaces, ISO files, and CPU or memory allocation. Shutdown first uses a
graceful guest request and offers a forced stop as a fallback when needed.

## Who is runvard for?

runvard is useful for:

- Homelab operators
- Home servers and private infrastructure
- Self-hosting setups
- Small internal server environments
- Administrators who want a compact web interface for common tasks
- Users who want Docker, backups, monitoring, and system maintenance in one place

runvard is not a public hosting panel for third-party customers and it is not a
multi-tenant platform. It is intended for controlled, private server environments.

## Security

runvard can perform privileged operations on the host. For that reason, it should
only be used on a trusted local network or behind a securely configured reverse
proxy.

Recommended practices:

- Allow access only for trusted users
- Use strong credentials
- Run it behind a VPN, local network, or secured reverse proxy
- Do not expose it directly to the public internet without protection
- Keep the system updated and maintain regular backups

The built-in terminal is an isolated, time-limited root shell and requires a fresh
administrator password confirmation before each connection. Do not expose runvard's
upstream HTTP port directly to an untrusted network.

## Installation

Installation instructions are available in [INSTALLATION.md](INSTALLATION.md).

## License

runvard is free for private, personal, non-commercial use. Commercial, business,
corporate, institutional, or revenue-generating use requires prior written
permission. See [LICENSE](LICENSE) for details.

## In short

runvard turns a Debian or Ubuntu server into a private control center for system
operations, Docker, apps, storage, networking, security, backups, monitoring, and
maintenance.
