# Screenshot Capture Checklist

This directory holds every screenshot referenced by the AthenaScout
user-facing documentation. Until each PNG below is captured and
dropped in here, the corresponding doc will render with a broken-image
placeholder on GitHub — which is intentional, so it's obvious at a
glance what's still missing.

## How to capture

- **Resolution:** 1920×1080 or 1440×900. Higher resolutions
  downscale gracefully on GitHub.
- **Browser zoom:** 100%.
- **Theme:** Dark or dim for the hero shot (looks the most
  polished). Other shots can use whichever theme reads clearest.
- **Format:** PNG, lossless.
- **Sensitive data:** Hide real personal info (email addresses, MAM
  tokens — but tokens shouldn't ever be visible on screen anyway).
  Test data with fictional book/author names is fine; you don't need
  to scrub real titles.
- **Crop:** Tight to the relevant UI. Drop browser chrome unless it
  adds context.
- **Filenames:** Use the exact names listed below — the docs
  reference them by name and a typo means a broken image.

## Required screenshots

| # | Filename | Used in | What to capture |
|---|---|---|---|
| 1 | `dashboard-hero.png` | `README.md` (top hero), `docs/first-run.md` | Main dashboard, dim or dark theme. Library stats, scan widget (running or recently complete), and the main nav all visible. This is the showcase image — make it look good. |
| 2 | `themes-showcase.gif` | `README.md` (themes section) | An animated GIF cycling through the dashboard in the three themes (light, dark, dim). The README markdown reference uses the `.gif` extension; GitHub renders animated GIFs inline. |
| 3 | `database-editor.png` | `README.md` (power-user section) | The DB editor with a table loaded — ideally something visually rich like the books table with multiple rows visible. Shows off the inline-edit and FK-resolution features. |
| 4 | `first-run-wizard.png` | `docs/first-run.md` | The admin account creation form on first launch. Empty fields are fine. |
| 5 | `dashboard-library-switcher.png` | `docs/first-run.md` | Dashboard with the library switcher dropdown **open**, showing at least two libraries. Skip if you only have one library — the markdown reference can be removed from `first-run.md` if so. |
| 6 | `settings-sources.png` | `docs/first-run.md` | The Settings → Sources section with all four sources visible (Goodreads, Hardcover, Kobo, MyAnonamouse). Toggles can be in any state. |
| 7 | `author-scan-results.png` | `docs/first-run.md` | An author detail page after a successful scan. Owned, Missing, and Upcoming sections should all have at least a few entries each — pick a productive author from your library. |
| 8 | `missing-page.png` | `docs/first-run.md` | The aggregated Missing books view. Some filters and a few rows visible. |
| 9 | `suggestions-page.png` | `docs/first-run.md` | The Suggestions page showing several pending series-consensus disagreements with current vs suggested values and the source-list chips. You currently have ~222 pending suggestions, so this should be easy to capture with real content. |
| 10 | `mam-settings.png` | `docs/mam-integration.md` | Settings → Sources → MyAnonamouse panel. **Make sure no real session token is visible** — clear or blur the token field before capturing. |
| 11 | `mam-page.png` | `docs/mam-integration.md` | The MAM page with the upload / download / missing-everywhere tabs visible and at least a few rows in the active tab. |
| 12 | `auth-login.png` | `docs/auth-deployment.md` | The login screen (after first-run is complete and you've logged out). Empty form. |

## Optional polish

- **`logo.png`** (square, ~512×512) or **`banner.png`** (wide,
  ~1280×320) — drop one of these in this directory if you have or
  want to create a project logo. The README hero block can be
  updated to reference it above the dashboard screenshot.

## Verifying

When you finish capturing, this is the verification command:

```bash
ls docs/images/*.png | wc -l
```

Should show 12 (plus one more if you added a logo or banner).
