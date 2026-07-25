# Connected Servers Dashboard Design

## Status

Implemented and verified locally.

## Objective

Show connected runvard servers directly on the dashboard whenever at least one
additional server is connected. Keep the existing dashboard centered and use
otherwise unused desktop space without turning the servers into a separate
navigation sidebar.

The section is labelled **Verbundene Server**. Existing internal `federation`
names in APIs and modules may remain unchanged.

## Information architecture

Each connected-server tile contains:

- server name;
- green, amber, or red status dot;
- update badge only when updates are available;
- CPU value with a violet miniature sparkline;
- RAM value with a cyan miniature sparkline;
- network value with a green miniature sparkline;
- a small external-tab indicator.

CPU, RAM, and network are arranged as three miniature columns side by side
inside the lower part of each card. All connected-server cards have exactly the
same height, including offline cards.

The words `Aktuell` and `Offline` are not shown. An offline server uses a red
status dot, em dashes for current values, and muted dashed or last-known
sparklines.

The complete tile is clickable and opens the server through the existing SSO
flow in a new browser tab. The external-tab icon is a small, low-emphasis hint,
not a separate action.

### Typography

The connected-server tiles reuse the existing dashboard type scale:

- server name: `12px` at weight `600`, matching `.tile-name`;
- section title: `11px`, matching the muted uppercase labels on the KPI cards;
- metric labels and values: `11px`, matching `.widget .sub`;
- update count: `11px` in a compact low-emphasis badge;
- plus glyph: `12px` inside a compact `22px` control;
- external-tab indicator: `8–9px`, thin and visually subordinate;
- health indicator: `6px`, matching the status dots on dashboard app tiles.

## Layout

### Wide desktop

At 1740 CSS pixels and wider:

- the existing main dashboard retains its centered `max-width` layout;
- connected servers use the otherwise empty right margin without changing the
  horizontal position of the main dashboard;
- the section contains one vertical column of broad horizontal cards;
- the title **Verbundene Server** sits directly above the first card;
- the bottom edge of the first server card aligns exactly with the bottom edge
  of the large `CPU`, `RAM`, `Storage`, and `Network` KPI cards;
- every server card has exactly the same width, height, radius, border,
  internal padding, and vertical gap;
- CPU, RAM, and network form three equal columns within each broad card;
- additional servers continue directly below in the same vertical rhythm;
- there is no enclosing background or vertical separator;
- the section is shown only when at least one remote server exists.

The page scrolls normally as the card list grows to its supported maximum of 20
connected servers.

### Desktop and tablet

Between 761 and 1739 CSS pixels, the server cards move below the main dashboard.
They use a two-column layout where space permits and retain the same fixed card
height and internal three-column metric layout. The main dashboard remains
centered.

### Mobile

At 760 CSS pixels and below:

- the server cards appear below the normal dashboard content;
- cards use one column and nearly the full content width;
- the first three servers are initially visible;
- an explicit “show more” action reveals the remaining servers;
- long press activates drag-and-drop so vertical page scrolling remains safe.

## Controls and interactions

- A compact violet `+` is positioned at the upper-right edge of the section
  title and opens the existing connect-server modal. It is hidden at rest and
  fades in only while the card list is hovered or keyboard-focused. On touch
  layouts it remains visible because hover is unavailable.
- Tiles can be reordered by drag-and-drop without a visible sort control or
  drag-grip icon.
- The saved order is shared across responsive layouts.
- Desktop dragging starts directly from the card surface.
- Touch dragging requires a long press.
- Hover uses a subtle accent border and slight elevation.
- Automatic status refreshes never reorder cards.

The existing **Server** dashboard tile remains available for setup,
administration, pairing codes, settings, and detailed status.

## Data and refresh behavior

The tiles reuse the existing federation overview/status data and SSO endpoint.
The status payload must provide, per remote server:

- current health state;
- available update count;
- CPU percentage;
- RAM percentage;
- network throughput;
- short bounded history for each sparkline.

Refreshes should update values in place and avoid rebuilding the complete tile
list. Repeated failures retain the last known history, mark the server red, and
replace current values with em dashes.

## Non-functional requirements

- No additional authentication mechanism; reuse existing SSO and permissions.
- Never expose pairing secrets or federation credentials in dashboard data.
- Polling must remain lightweight for 20 remote servers.
- A slow or unavailable server must not delay rendering the rest of the list.
- Keyboard users can focus, open, and reorder cards.
- Status is never communicated by color alone: unavailable metric values and
  accessible labels expose the state to assistive technology.
- All new visible strings require translations for `de`, `en`, `fr`, `it`,
  `es`, and `pt`.

## Empty and error states

- Zero remote servers: render no connected-server section.
- Overview request fails: preserve the previous list and mark its data stale.
- Server goes offline: keep the tile and its position.
- SSO launch fails: keep the current page open and show a translated error.
- Connect action is hidden or disabled for users without administrative
  permission.

## Verification

- Translation parity test for every new string.
- Responsive checks at wide desktop, normal desktop, tablet, and mobile widths.
- Main-dashboard center position regression test.
- Conditional rendering test for zero and one remote server.
- Tile click opens SSO in a new tab.
- Plus opens the connect-server modal.
- Drag order persists and survives refresh.
- Touch long-press does not interfere with normal scrolling.
- Offline and stale-data states remain accessible.
- Twenty-server rendering and polling performance smoke test.

## Decision log

1. **Broad cards in one vertical column.** Connected servers use the unused
   right margin as a calm stacked list rather than extending the navigation
   grid.
2. **Main dashboard remains centered.** The optional rail must not shift the
   established dashboard layout.
3. **Title without sidebar enclosure.** The label **Verbundene Server** provides
   orientation; no enclosing background or separator turns it into a separate
   sidebar.
4. **Exact vertical alignment.** The first server card ends on the same baseline
   as the large KPI cards. Every server card below it uses the same height and
   gap, creating a consistent visual rhythm.
5. **Compact cards with three sparklines.** CPU, RAM, and network provide useful
   context without reproducing the full monitoring screen.
6. **Status text removed.** Colored dots plus values, em dashes, and accessible
   labels communicate state with less visual noise.
7. **Whole-card SSO action.** A small external-tab icon indicates the result but
   is not a separate click target.
8. **Invisible manual ordering.** Drag-and-drop is available without permanent
   controls; touch requires a long press.
9. **Responsive relocation.** The list moves below the dashboard when the right
   margin is insufficient and becomes a compact expandable list on mobile.
10. **Server tile retained.** Detailed administration remains separate from the
   glanceable dashboard cards.
11. **Existing typography scale reused.** Server names match dashboard tile
    names, while metrics match widget subtitles; no server text is reduced below
    the established readable sizes.
12. **Secondary controls stay quiet.** Health dots match the existing 6px app
    status dots, external-tab indicators are only 8–9px, and the connect action
    is revealed on hover/focus instead of remaining visible on desktop.
