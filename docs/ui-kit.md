# Operations UI kit

Every Operations page is moving onto one set of tokens and components so the
product looks and behaves as one. Attendance, AI Controls and Bookings Analytics are on it.

## Where things are

| What | File |
|---|---|
| Tokens: font, type scale, accent, tones, surfaces, control heights, layers | `dashboard/src/styles/tokens.css` |
| Kit styles (`ui-*` classes, tokens only, no literal colours) | `dashboard/src/styles/ui.css` |
| Components | `dashboard/src/components/ui/` |
| Visual harness (mock API, screenshots, pixel compare) | `dashboard/visual/` |

## Components

- `Button` — `primary` (one per card), `secondary` (default), `ghost`, `danger`, `link`; sizes `sm` 32, `md` 36, `lg` 44; 44px on touch screens; `loading` blocks double presses.
- `Card` (title, description, actions, footer), `StatTile`, `CardGrid` (3 → 2 → 1 columns).
- `Badge` and `Alert` with tones `neutral`, `info`, `success`, `warning`, `danger`; `Toast`; `EmptyState`.
- `Field` + `Input` (label always present for screen readers), `FormRow` (stacks on phones).
- `List` + `ListRow` (title, meta, actions).
- `DataTable` — scrolls inside its card on tablets; on phones each row becomes a card of "label: value" lines. Mark one column `primary` to title the row's card.
- `Dialog` — focus moves in, Tab stays inside, Escape closes, focus returns.
- `Tabs` — sections of one page; the current tab carries the accent underline. The page renders the panels, so it can keep both mounted.
- `Disclosure` — detail behind a quiet summary line (`details`/`summary`).

## Rules for a page on the kit

- Colours from tokens only. Blue is for the primary action, links, focus and the current item (the selected tab, the selected row); everything else is a neutral surface.
- Interview outcomes use the tones plus `--hue-orange`, `--hue-violet` and `--hue-yellow` (technical issue, rescheduled, re-service). Chart series colours are data and stay in the component.
- Font sizes from `--fs-*` (smallest 11px), spacing from `--space-*`, radii from `--radius-*`.
- Breakpoints: phone < 600px, tablet 600–899px, desktop ≥ 900px (the shell's drawer switch).
- Keep a page's wording and behaviour; a migration changes presentation only.
- Remove a page's old private CSS only after checking nothing in `dashboard/src` or `dashboard/visual` still refers to it.
- index.css styles every input on screens up to 900px (`input:not(...)`, padding 8px 12px). An input with an icon inside needs a selector that outranks it, e.g. `input.ui-input[type="search"]`.
- A page drops shared page scopes it only borrowed (AI Controls no longer carries `.sot-mailboxes-page`), so another page's rules cannot restyle kit components on it.

## Moving a page

1. Add fixtures for the page's API calls to `visual/fixtures.js` (invented people, fixed dates).
2. Capture before, on `main`: `npx vite --port 3100`, then `node visual/capture.mjs --out ../.visual/before --pages "<Sidebar label>:admin"` (`npm i --no-save playwright-core` once; it drives the installed Edge). `"<Sidebar label>:admin:<Tab>"` opens one of the page's own tabs first; `"~Text"` matches a button whose name starts with it (one that carries a count).
3. Move the page onto the kit and run its tests.
4. Capture after, then `node visual/compare.mjs --before ../.visual/before --after ../.visual/after --out ../.visual/compare.html`.
5. Check 390, 768, 1024 and 1440px, both roles where the page differs by role, and that no other page changed.
6. Full `npx vitest run` and `npx vite build`.
