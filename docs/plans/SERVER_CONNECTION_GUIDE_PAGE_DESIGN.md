# Server Connection Guide Page

## Status

Approved for implementation.

## Understanding Summary

- The compact **Connect server** picker remains the place where a connection
  is started.
- A single, unobtrusive **Guide** link is placed beside the **Status and
  links** section heading.
- The guide opens in a new browser tab so the partially completed server
  picker is not replaced.
- One internal runvard page documents runvard peers, Proxmox, Linux and OMV,
  Windows Server, and generic server links.
- Commands are copyable individually and identify the machine on which they
  must run.
- The page follows the selected runvard language and supports all six existing
  locales.
- No token, password, private key, or other user secret is rendered or stored
  by the guide.

## Assumptions

- The guide is a static page mounted below `/static/` and requires no API.
- Approximately 20 managed servers does not change the guide page's scale or
  performance requirements.
- Guide content changes infrequently and is maintained with the application.
- Official documentation links open separately and use `noopener` protection.
- Placeholder IP addresses and names are visibly marked and never contain real
  credentials.

## Approaches Considered

1. **Internal standalone guide page — selected.** Keeps the picker compact,
   preserves context in the original tab, supports copy actions and local
   translations, and ships with the application.
2. **Guide modal — rejected.** Reintroduces the nested modal experience that
   was deliberately removed and competes with the connection form.
3. **External documentation link — rejected.** Cannot document runvard's exact
   field mapping, connection tests, or supported security constraints.

## Design

The picker renders one semantic anchor beside the **Status and links** heading.
It targets `/static/server-connection-guide.html`, opens with `target="_blank"`,
and uses `rel="noopener noreferrer"`.

The guide page uses a restrained operations-manual layout matching runvard:

- compact product header and language selector;
- introductory security notice;
- sticky section navigation on larger screens and a horizontal scroller on
  mobile;
- one semantic article per connection type;
- numbered steps, field mapping, expected test results, and security notes;
- one copy button per command with an accessible status announcement.

Mobile is the base layout. At wider breakpoints, navigation and guide content
form two columns. Buttons and links keep at least a 44 px touch target.

## Security and Reliability

- Commands use placeholders such as `PROXMOX-IP`, `OMV-IP`, and
  `RUNVARD-IP`; the page never asks users to paste secrets into it.
- Token tests use a hidden shell prompt and clear the temporary shell
  variable afterward.
- SSH instructions distinguish public and private keys and warn against
  sharing private material.
- Copying uses the Clipboard API with a local textarea fallback.
- The page requires JavaScript to render localized instructions and copy
  controls; a `noscript` fallback links to the repository documentation.

## Testing Strategy

- Assert the picker link opens the local guide in a protected new tab.
- Assert the page covers all five connection types and the exact Runvard form
  mappings.
- Assert every command has an accessible copy action and no embedded secret.
- Assert all six locale dictionaries have identical keys.
- Verify semantic landmarks, heading order, live copy feedback, mobile-first
  CSS, and touch target sizes.
- Run the full Python suite and the existing i18n checker, then inspect the
  guide at desktop and mobile viewport sizes.

## Decision Log

- Use one guide page instead of per-server information buttons.
- Place the link in the external-server section heading, where it is relevant
  without adding noise to every card.
- Include the trusted runvard connection as well as all external connectors.
- Keep the page static, local, translated, and independent from credentials.
