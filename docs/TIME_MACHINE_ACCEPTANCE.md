# Time Machine acceptance and load runbook

This runbook is the release gate for runvard Time Machine support. Automated
server tests do not prove that Apple can restore an encrypted network backup.

## Preconditions

- Use a dedicated test Mac and a non-production runvard target.
- Enable **Encrypt Backup** on the Mac and retain its separate encryption key.
- Verify that the target has a hard quota on ZFS/Btrfs or a clearly reported
  Samba limit on a directory backend.
- Change target capacity and retention in the UI. Confirm that the advertised
  size changes and, on ZFS/Btrfs, that the native quota matches it with the
  reserved headroom preserved.
- Attempt to reduce capacity below currently allocated data and confirm that
  runvard refuses the change without altering Samba or the native quota.
- Confirm that successful target/replication policy changes appear under
  **Recent changes** with the administrator and old/new values.
- Test LAN discovery and the direct `smb://host/share` address over the actual
  VPN used in production.

## Linux host acceptance

On the runvard host, supply the target account password through the environment
and run the read-only host gate. Adding a second account verifies cross-user
denial without writing to the share:

```bash
read -rsp 'Target SMB password: ' RUNVARD_TM_SMB_PASSWORD; export RUNVARD_TM_SMB_PASSWORD; echo
read -rsp 'Other SMB password: ' RUNVARD_TM_CROSS_PASSWORD; export RUNVARD_TM_CROSS_PASSWORD; echo
scripts/time-machine-linux-acceptance.sh --share //localhost/tm-test --user tm-test --cross-user tm-other
unset RUNVARD_TM_SMB_PASSWORD RUNVARD_TM_CROSS_PASSWORD
```

This checks the complete Samba configuration, per-share Time Machine and SMB
encryption settings, private-network restriction, authenticated encrypted
listing, cross-user denial, Bonjour advertisement, and the persistent
maintenance timer. It uses `smbclient -c 'ls'` only and does not upload or
delete data.

## Mac acceptance

Run on each supported macOS release, first read-only and then explicitly apply
the destination:

```bash
scripts/time-machine-macos-acceptance.sh --share smb://runvard.example/tm-test
scripts/time-machine-macos-acceptance.sh --share smb://runvard.example/tm-test --apply-destination
```

After a complete backup, run the Apple verification gate:

```bash
scripts/time-machine-macos-acceptance.sh --share smb://runvard.example/tm-test --verify
```

Pass criteria:

1. Bonjour discovery works on the LAN; direct SMB works over VPN.
2. A first backup and a smaller incremental backup complete after disconnects.
3. The destination reconnects after both Mac and server restarts.
4. `tmutil verifybackups` succeeds.
5. Restore several files and folders through Time Machine.
6. Restore a test Mac with Migration Assistant. This destructive hardware test
   is manual and must be signed off outside CI.
7. Promote a passive replica only after disabling the source, reconnect the Mac,
   verify the backup, and repeat a file restore.

Record macOS version, model, connection type, backup size, elapsed time and
restore result. The intended support matrix is macOS 11 and newer.

## Concurrent load gate

The design ceiling is **20 registered** Macs and **10 concurrent** backups. Mount
a dedicated test share, ensure it contains no production data, then run:

```bash
scripts/time-machine-load-test.py --mount /Volumes/tm-load --clients 10 --size-mib 1024
```

The tool creates a unique marked directory, runs concurrent write/read checksum
checks, and removes only that directory. Use `--keep` only for diagnosis. Pass
requires no checksum or I/O failures, usable UI/API response during the run, no
SMB crashes, no unbounded worker queue, and pool usage below the 95% stop limit.

Finally register 20 targets (without starting all at once), confirm per-target
quotas and status cards, and retain the JSON load report with the release notes.

## Remote replica onboarding

1. On the source runvard, open **Backups → Time Machine → Remote setup** and
   generate the managed Ed25519 source identity.
2. On the destination runvard, use **Authorize source key** and paste only that
   public key. The destination stores it with `restrict` and a receiver-only
   ForceCommand; it cannot open a shell, forward ports or allocate a terminal.
3. Obtain the destination SSH host key on a trusted channel. Verify its SHA256
   fingerprint out of band, then use **Pin host key** on the source. Do not copy
   an unverified fingerprint from the same network session.
4. Create the remote replica with a private LAN/VPN host, the
   `runvard-replica` account and receiver root `/srv/runvard-replicas`.
5. Run once and confirm that the destination contains only two marked,
   completed passive generations. Interrupted `.incomplete-*` directories are
   not eligible for import.
6. Disable a queued replication and verify that its job becomes cancelled.
   Re-enable it, change its hour and bandwidth, and verify that the next
   scheduled run uses the new policy.
7. Verify that target removal disables its replications and is refused while a
   dependent replication job is running.
8. For failover, disable or remove the original writable target first, confirm
   source unavailability, import the received generation on the destination,
   then complete the Apple verification and restore gates above.
