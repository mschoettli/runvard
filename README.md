# runvard

runvard is a web-based server control panel for Debian and Ubuntu systems.
It brings monitoring, system administration, Docker, apps, storage, networking,
security, backups, and maintenance into one private interface.

runvard is built for people who operate their own servers, homelabs, and small
infrastructure setups and want direct control without constantly switching between
terminal commands, log files, Docker tools, and separate admin interfaces.

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
- Work with files and shares directly in the browser
- Administer storage, disks, and filesystems
- Configure networking, firewall rules, and interfaces
- Manage users, groups, SSH keys, and permissions
- Schedule and run backups
- Inspect logs, alerts, and audit entries
- Apply system updates and maintenance tasks
- Manage virtual machines
- Organize services and links on a customizable dashboard

## Features

### Dashboard

The dashboard is the main starting point in runvard. It shows important services,
installed apps, Compose projects, and custom links as tiles. Ordering and visibility
can be adjusted so frequently used services stay easy to reach.

### Languages

runvard supports multiple interface languages. The installer can currently be run
in English or German, and the selected installer language is stored as the initial
web interface language. Users can still switch the interface language later from
the web UI.

The main web interface includes language options for English, German, French,
Italian, Spanish, and Portuguese. Translation coverage may vary in deeper system
tools, but the interface is designed to fall back safely to English where needed.

### System monitoring

runvard displays live system information such as CPU usage, memory, disks, network
activity, temperatures, processes, and historical metrics. This makes it easier to
spot load, bottlenecks, and unusual behavior.

### Docker and apps

Docker containers can be started, stopped, created, and monitored. runvard shows
container logs, runtime information, images, volumes, and resource limits. Docker
Compose projects can also be created, edited, and managed.

runvard also includes an app section for self-hosted services based on Docker
Compose, making common homelab and server applications easier to deploy.

### Files and shares

The built-in file manager provides browser-based access to files and folders. Files
and directories can be created, moved, copied, renamed, deleted, uploaded,
downloaded, archived, and extracted.

runvard also supports share and mount workflows, including Samba- and NFS-related
operations.

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

Backup jobs can be created, scheduled, and run manually. A backup history shows
successful and failed runs.

### Virtual machines

When virtualization is available on the server, runvard can manage virtual machines.
This includes starting and stopping VMs, snapshots, storage pools, virtual disks,
network interfaces, and ISO files.

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

## Installation

Installation instructions are available in [INSTALLATION.md](INSTALLATION.md).

## In short

runvard turns a Debian or Ubuntu server into a private control center for system
operations, Docker, apps, storage, networking, security, backups, monitoring, and
maintenance.
