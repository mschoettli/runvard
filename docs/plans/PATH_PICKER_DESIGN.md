# Unified Path Picker

## Understanding

- Replace manual server-path entry with a simple visual picker wherever the path can be discovered locally.
- Use one reusable dialog with folder, file, and block-device modes.
- Keep normal mode limited to safe storage roots and detected mounted storage.
- Expose the complete filesystem and free-form path entry only in expert mode.
- Validate existence, type, readability, writability, and purpose before accepting a selection.
- Allow folder creation only inside an allowed, writable location.
- Support Classic and Modern themes, keyboard navigation, mobile layouts, and all six Runvard locales.

## Assumptions and non-functional requirements

- Runvard is normally operated by one administrator at a time.
- Directory contents are loaded lazily and capped per request to keep large directories responsive.
- Unavailable or slow mounts return a bounded error instead of blocking the UI indefinitely.
- The server remains the authority for safe roots and expert-mode access; hiding controls in JavaScript is not a security boundary.
- Symlinks are resolved before authorization so they cannot escape an allowed root.
- The picker does not delete, move, rename, or upload files.
- Remote NFS exports and paths inside a container are not local filesystem paths. They require purpose-specific discovery or remain clearly identified expert inputs rather than being misrepresented by a local browser.

## Approaches considered

1. **Unified purpose-aware picker (selected).** One component and API with folder, file, and device modes. Lowest long-term inconsistency and one security boundary.
2. **Separate wizard per feature.** More tailored, but duplicates navigation, validation, translations, and accessibility behavior.
3. **Reuse the full Files modal.** Reuses navigation but carries unrelated file-management actions and cannot naturally represent device selection or purpose policies.

## Final design

### Server

`modules/path_picker.py` owns path normalization, safe-root discovery, purpose policies, browsing, validation, and folder creation. Normal sessions can only resolve paths below a purpose-specific safe root. Expert sessions can start at `/` and may enter an absolute path, except for blocked virtual system trees.

The API exposes read-only roots, browse, and validation endpoints plus an admin-only create-folder endpoint. The server derives expert status from the signed session and never accepts an `expert=true` client override.

### Client

The form schema gains a `picker` descriptor. `openForm` renders the normal text input as a selected-path display plus an “Choose” button. Free typing is enabled only in expert mode. The picker opens above the existing form, traps focus, restores focus on close, supports breadcrumbs, safe-location cards, lazy folder rows, status messages, and folder creation.

The same component is used by backup, SMB/NFS mountpoints, swap, disk/LUKS/Btrfs mountpoints, Docker host-volume paths, VM images and storage pools, Time Machine storage roots, Samba/NFS local share paths, AppArmor profiles, and app-install host storage where the value represents a local server path. Remote or container-internal values use dedicated choices and explanatory copy.

### Accessibility and responsive behavior

- Native buttons and connected labels; no click-only generic elements.
- `role="dialog"`, `aria-modal`, labelled title, live validation status, Escape close, and Tab focus trap.
- Minimum 44px controls, one-column mobile layout, two-pane enhancement at the existing desktop breakpoint.
- Reduced-motion rules inherit Runvard’s existing policy.

### Error handling

- Blocked or escaped paths return a generic 403/400 without exposing directory contents.
- Missing, wrong-type, read-only, and unreachable selections have distinct localized messages.
- The confirmation button remains disabled until the current purpose policy passes.
- Folder creation rejects empty names, separators, `.`/`..`, existing entries, and disallowed parents.

## Decision log

| Decision | Alternatives | Reason |
|---|---|---|
| Full filesystem only in expert mode | Always show `/` | Protect beginners from system paths. |
| Manual path entry only in expert mode | Keep every text field | Normal mode should not require Linux path knowledge. |
| One picker with folder/file/device modes | Per-feature dialogs | Consistent UX, validation, translations, and maintenance. |
| Server-enforced safe roots | Client-only hiding | Client state can be manipulated. |
| Purpose policies | Generic unrestricted browser | VM images, writable destinations, and devices need different validity rules. |
| Allow folder creation in safe writable roots | Use Files first | Keeps the task in context without adding full file management. |
| Do not pretend remote/container paths are local | Browse everything through one tree | Remote exports and container paths have different namespaces. |

## Key risks

- Path traversal or symlink escape: mitigated by `realpath` plus `commonpath` checks on every request.
- Accidental system writes: mitigated by safe roots, purpose policies, and expert-session enforcement.
- Slow mounts: mitigated by lazy loading, entry caps, and bounded filesystem operations.
- Mixed-language UI: blocked by `scripts/check-i18n.mjs`, locale tests, and browser checks in all six languages.
