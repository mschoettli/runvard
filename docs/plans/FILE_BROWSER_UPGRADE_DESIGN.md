# File browser upgrade design

Status: Accepted on 2026-08-01

## Understanding summary

- Upgrade Runvard's existing file browser with resumable uploads, archive downloads,
  persistent network mounts, richer sharing, encrypted folders, scalable directory
  browsing, richer previews, and a safer recycle bin.
- Preserve the existing FastAPI and vanilla JavaScript architecture. No frontend
  framework migration is part of this project.
- Keep existing file operations and API consumers compatible while introducing a
  versioned API for the new behavior.
- Retain Runvard's system-administration focus and its direct access to selected
  Linux filesystem paths.
- Deliver a responsive, accessible UI with clear loading, empty, locked, offline,
  conflict, and error states.
- Support directories with approximately 100,000 entries and large uploads that
  survive page reloads and temporary connection loss.
- Verify the result with backend tests, browser flows, and screenshots at mobile,
  tablet, laptop, and desktop sizes.

## Scope

1. Resumable, cancellable, and parallel uploads.
2. Folder and multi-selection downloads as streamed ZIP archives.
3. Persistent SMB and NFS mount profiles with connect, disconnect, edit, test,
   delete, and reconnect behavior.
4. Member or guest access and read or read-write permission controls for Samba
   shares.
5. Public links with optional password, expiry, download limit, revocation, and a
   management view.
6. Encrypted folders backed by `gocryptfs`.
7. Cursor-based directory APIs and a virtualized grid/list UI.
8. Image thumbnails, HEIC where supported, audio playback, and improved preview
   states while preserving image, video, PDF, text, and Markdown support.
9. Recycle-bin retention, size limits, individual purge, and restore conflict
   handling.

Cloud providers such as Google Drive, Dropbox, and OneDrive are not part of this
scope.

## Non-functional requirements

### Performance and scale

- Target directories containing approximately 100,000 entries.
- Keep only visible file rows/cards and a small buffer in the DOM.
- Upload chunks default to 8 MiB with no more than three concurrent requests per
  upload.
- Use short-lived directory indexes and invalidate them after Runvard-managed
  mutations.

### Security and privacy

- Do not invent encryption primitives.
- Do not persist plaintext share passwords, mount credentials, vault passwords, or
  recovery keys in SQLite or JSON.
- Do not expose secrets in command-line arguments.
- Validate mount options and generated Samba configuration before activation.
- Preserve Runvard's blocked and read-only system path protections.

### Reliability

- Persist transfer state and recover safe jobs after application restarts.
- Mark ambiguous interrupted moves as requiring inspection instead of guessing.
- Use atomic finalization for uploads and metadata changes.
- Network mount failures must not block Runvard startup.

### Maintenance

- Remain within the current Python/FastAPI and vanilla HTML/CSS/JavaScript stack.
- Introduce narrowly owned modules rather than expanding the existing monolithic
  `modules/files.py` indefinitely.
- Keep the current API as a compatibility facade during migration.

## Architecture

The file browser remains part of the Runvard process as a modular monolith:

- `files_core`: safe path resolution, metadata, directory pagination, and search.
- `file_transfers`: upload sessions, background jobs, archive downloads, and
  progress.
- `file_mounts`: SMB/NFS profiles and lifecycle management.
- `file_shares`: Samba permissions and public links.
- `file_vaults`: encrypted-folder lifecycle.
- `file_previews`: thumbnails and media/text preview metadata.

Metadata is stored in `/opt/runvard/data/files.db`. Files remain in their original
filesystem locations. Database migrations are idempotent. Existing
`file_jobs.json`, `shares.json`, and trash metadata are imported once and retained
as migration backups.

Existing endpoints call the new services through a compatibility layer. New
behavior is exposed under `/api/files/v2`.

## Transfers and directory browsing

An upload starts by creating a durable session containing the safe destination,
name, total size, chunk size, and optional SHA-256 digest. The client queries which
chunks exist, uploads the missing chunks, and asks the server to finalize the file.
Finalization assembles into a temporary file, verifies size and digest, and performs
an atomic rename. Incomplete upload sessions expire after seven days.

Copy, move, delete, ZIP, and archive-download operations run as persisted jobs.
They may be paused, resumed, cancelled, or retried. A restarted server resumes safe
copy/archive work. Interrupted moves use a `needs_attention` state if completion
cannot be proven.

Directory requests accept a cursor, limit, sort field, and sort direction. Search
runs as an abortable job and yields batches of results. The UI virtualizes the
visible grid or table so large directories do not create an equally large DOM.

## Recycle bin

The default retention period is 30 days. Administrators may configure retention
and a size limit. Users can restore, permanently remove one item, or empty the bin.
Restore conflicts offer replace, restore with a unique name, or choose another
destination. Automatic cleanup removes the oldest expired entries first.

## Mounts

Mount profiles store the display name, type, source, target, options, write mode,
auto-connect preference, last error, and runtime state. SMB credentials are stored
in dedicated mode-0600 files. Mount commands reference those files rather than
embedding passwords in process arguments. NFS options are allow-listed.

Profiles can be tested, connected, disconnected, edited, and deleted. Auto-connect
uses bounded retries and reports degraded state without delaying application
startup indefinitely.

## Shares and public links

Samba shares support guest or named-member access plus read or read-write mode.
Runvard owns only its generated Samba include. `testparm` must succeed before a
configuration is activated. Samba passwords are handled by Samba tooling and are
not retained by Runvard.

Public links support files and folders. Folder downloads are streamed as ZIP.
Links may have a password, expiry, and download limit. Only a hash of the bearer
token is stored. The full token is displayed once. Access is rate-limited and
audited without recording file contents or passwords.

## Encrypted folders

Encrypted folders use `gocryptfs`. Runvard guides creation, recovery-key handling,
unlock, and lock operations without storing vault passwords or recovery keys.
Vaults remain locked after restart. Before locking, Runvard detects active file
jobs and open processes. Forced locking requires explicit destructive-action
confirmation. If `gocryptfs` is unavailable, Runvard reports the missing package
and does not fall back to custom encryption.

## User experience

The redesigned browser has three adaptive regions:

1. A places rail for local locations, mount profiles, vaults, and the recycle bin.
2. A primary workspace with breadcrumb, search, commands, and the virtualized
   grid/list.
3. A collapsible inspector for preview, metadata, permissions, links, and contextual
   actions.

A persistent transfer center at the bottom displays current and recent operations
with pause, resume, cancel, retry, and error detail. File cards use thumbnails where
available. Audio has a dedicated player. Existing image, video, PDF, Markdown, and
text behavior remains available.

On mobile, the places rail becomes a sheet, the inspector becomes a detail view,
and selection commands become a bottom action bar. Primary touch targets are at
least 44 by 44 CSS pixels.

## Error handling and edge cases

- A duplicate destination receives an explicit conflict decision or a deterministic
  unique name; it is never silently overwritten.
- Missing mount sources, expired links, exhausted download limits, unavailable
  preview decoders, locked vaults, and interrupted jobs have distinct error codes
  and UI states.
- Archive extraction continues to reject path traversal entries.
- Symlinks are not followed into blocked paths.
- Jobs record actionable errors while omitting passwords, bearer tokens, and file
  contents.

## Testing strategy

Implementation follows red-green-refactor by functional slice. Backend tests cover
path safety, upload chunks, restart recovery, checksums, conflicts, archive
streaming, token policy, share rendering, mount option validation, vault lifecycle,
pagination, and recycle-bin cleanup. Browser verification covers selection,
resuming an upload, the transfer center, link management, locked states, preview
behavior, and navigation.

Responsive visual checks run at 375, 768, 1024, and 1440 CSS pixels. Final desktop
and mobile screenshots show the file workspace, transfer center, mount management,
vault state, and link management.

## Decision log

1. Keep a modular monolith rather than add another service or rewrite the frontend.
2. Preserve the current stack and provide a V1 compatibility facade.
3. Use SQLite for concurrent, durable metadata instead of additional JSON stores.
4. Use 8 MiB chunks, three concurrent requests, and seven-day upload-session
   retention.
5. Use cursor-based APIs and DOM virtualization for large directories.
6. Default recycle-bin retention to 30 days.
7. Store mount secrets only in protected credential files.
8. Validate generated Samba configuration before activation.
9. Store hashes rather than raw public-link tokens.
10. Use `gocryptfs` and require manual unlock after restart.
11. Use a three-region responsive workspace and persistent transfer center.
12. Treat cloud-provider integration as a separate future project.

## Alternatives considered

### External file engine

Delegating transfers and sources to Rclone, tus, or File Browser would provide broad
features quickly, but would add services, split permissions and error handling, and
increase installation and maintenance complexity.

### Separate Files service and frontend rewrite

A standalone service and component-based frontend would provide a cleaner long-term
boundary, but would create the largest migration and regression risk. The modular
monolith preserves current behavior while leaving open a later extraction path.
