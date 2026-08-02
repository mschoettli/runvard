# Login Theme Design

## Understanding

- RunVard keeps one login flow but presents a matching login design for each UI theme.
- The existing dark login remains the Original variant.
- The Modern variant uses the light, calm glass design language of the modern dashboard.
- A visible `Original / Modern` control lets users choose before signing in.
- The choice persists in `localStorage` under the existing `runvard_ui_theme` key and carries into the authenticated UI.
- Authentication behavior, translations, error handling, and connection warnings remain unchanged.
- The page stays responsive, keyboard accessible, dependency-free, and inexpensive to maintain.

## Assumptions

- Original is the default when no theme preference exists.
- Both variants use the same HTML form and JavaScript login flow.
- Theme selection is not security-sensitive and remains a client-side preference.

## Final Design

`static/login.html` remains the only login page. A small inline bootstrap script reads the stored theme before the page renders and sets `data-ui-theme` on the document element. Theme-specific CSS changes presentation only; it does not duplicate or branch authentication logic.

A two-option theme switch is placed above the login card. It exposes pressed state to assistive technology, supports keyboard operation, uses touch targets of at least 44 pixels, and updates the stored preference immediately. The Original styles stay visually unchanged. The Modern styles introduce a light blue-gray atmosphere, restrained translucent surfaces, blue accents, quiet shadows, and clear focus states. Reduced-motion preferences disable non-essential transitions.

Verification covers both visual variants, preference persistence across reloads, successful and failed login states, keyboard interaction, and mobile and desktop viewport sizes.

## Decision Log

1. Use one page with two CSS design layers instead of separate routes to avoid duplicated authentication logic.
2. Show the theme switch before login so first-time and signed-out users can choose deliberately.
3. Preserve the Original design and implement Modern as scoped overrides.
4. Reuse `runvard_ui_theme` so the selected login and dashboard themes always agree.
