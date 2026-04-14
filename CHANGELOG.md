# Changelog

All notable changes to AthenaScout are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.2] — 2026-04-13

### Fixed

- **MAM Full Library Scan progress counters stuck at 0.** The full-
  scan loop was only updating `state._mam_scan_progress`'s
  scanned/found/possible/not_found counters AFTER each 400-book
  batch completed — a 5+ minute wait during which the Dashboard
  widget showed "0 of 2714 books · Found: 0 · Possible: 0 · Not
  found: 0" even though books were actually being scanned and
  committed to the DB. Fix: added an `on_progress` callback to
  `run_full_scan_batch()` that fires after every book with the
  running batch-local stats, and wired a closure in the router's
  `_full_scan_loop` that adds those onto per-batch baselines so
  the counters tick up in real time. The `current_book` field was
  already updating correctly via the existing `on_book` callback;
  unaffected. Manual (150-book batch) scan was never broken —
  this only affected the full-library path.
- **Stop button didn't unstick the running flag.** Cancelling a
  full scan during the 5-minute inter-batch sleep (the most
  common moment to hit Stop) raised `asyncio.CancelledError`
  inside the loop, which exited without resetting
  `state._mam_scan_progress["running"] = False`. The unified
  Dashboard widget then stayed stuck on "Paused, resuming soon"
  and every subsequent MAM scan attempt errored with "A MAM
  scan is already running" until container restart. Fix:
  wrapped the loop in `try/except asyncio.CancelledError` that
  flips `running: False` + `status: "cancelled"` before re-raising.
  Applies to cancellation at any `await` point in the loop, not
  just the inter-batch sleep.

## [1.1.1] — 2026-04-13

### Fixed

- **MAM endpoints couldn't read the session token after Settings save.**
  The Sprint 6 encrypted-store migration routed credential writes
  through the encrypted DB and blanked the original
  `settings.json` value. The MAM endpoint readers in
  `app/routers/mam.py` and the scheduled-scan loop in
  `app/main.py` still pulled directly from
  `s.get("mam_session_id")`, which is now always `""`. Visible
  symptoms: pasting a fresh token through Settings → Validate
  always returned "No MAM session ID configured", and every
  scheduled MAM scan silently no-op'd. Fix: every read goes
  through `_get_mam_token()` (in-memory → encrypted store →
  settings.json legacy fallback) and every gating check goes
  through the new `_mam_ready(s)` helper. The startup seed at
  `app/main.py:133` is unchanged — that's the one site where
  settings.json fallback IS correct.

  No data migration needed; existing tokens already in the
  encrypted store from v1.1.0 keep working.

## [1.1.0] — 2026-04-13

A meaty release. Three new metadata sources, smarter MAM matching,
auto-rotating MAM cookies, encrypted credential storage,
co-author / pen-name linking, push notifications, an in-app log
viewer, one-click handoff to Hermeece, and a full TypeScript
migration of the frontend.

### Added

#### New metadata sources

- **Amazon source** ([`app/sources/amazon.py`](app/sources/amazon.py))
  — author-centric scraper with audiobook detection (RPI cards +
  productSubtitle scan) and a junk-listing pre-filter that drops
  third-party seller titles, bracketed format suffixes, and
  "By AUTHOR — Title" sham listings before they reach the detail
  pages. Best at confirming standalone vs series.
- **IBDB source** ([`app/sources/ibdb.py`](app/sources/ibdb.py)) —
  supplementary REST source for ISBN and publisher backfill.
- **Google Books source** ([`app/sources/google_books.py`](app/sources/google_books.py))
  — supplementary REST source for ISBN, publisher, and description
  backfill. Daily quota is respected via `retries=0` so a stale
  scan can't burn through your allowance.

The orchestrator now walks a typed `SourceSpec` registry with
per-source `asyncio.wait_for` timeouts (60–300s per source) and a
global per-author wall-clock budget (15 min) so a stuck source
can't hang the pipeline. Source-priority order is now explicit and
configurable in one place: MAM → Goodreads → Amazon → Hardcover →
Kobo → IBDB → Google Books.

#### Author-linking (pen names + co-authors)

- New `pen_name_links` table with a `link_type` discriminator
  (`pen_name` | `co_author`). Backend dedup is identical for both
  link types — the label is purely UX.
- AuthorsPage gains a multi-select bulk action: select 2+ authors
  → "Link as Pen Names" or "Link as Co-Authors". The first
  selected author becomes the canonical identity; the rest become
  aliases.
- AuthorDetailPage's existing `+ pen name` button is now
  `+ link author` with a Pen Name / Co-Author toggle.
- A small `↔ N` chip appears next to any author with active links
  in the browse view.

This collapses noise from authors who write under multiple names
(William D. Arand ↔ Randi Darren) AND from authors who habitually
co-author with the same partner (J.N. Chaney + Christopher Hopper,
etc.) into one scan / dedup unit.

#### MAM improvements

- **Cookie auto-rotation.** AthenaScout intercepts the `Set-Cookie`
  header on every MAM response and persists rotated tokens to the
  encrypted credential store (debounced to once per minute). Paste
  your token once during setup, never re-paste again — no more
  "session expired" every two weeks.
- **Confidence scoring.** Match scoring now lives in
  [`app/scoring.py`](app/scoring.py) (shared with Hermeece): 70%
  title similarity + 30% author + series-name boost. ≥ 0.70 is
  **found**, 0.50–0.70 is **possible**.
- **Audiobook category rejection** — MAM results in the audiobook
  category no longer beat ebook results for ebook libraries (and
  vice versa).
- **Series bundle awareness** — bundles that ship every book in a
  series consistently land as `possible` rather than `found`.

#### Send to Hermeece

One-click handoff of a `found` MAM match to a [Hermeece](https://github.com/mnbaker117/Hermeece)
instance for automatic download + Calibre import. Available from:

- The book sidebar (single book)
- The MAM page list/grid views (per-row button)
- Multi-select on the MAM page (bulk; non-`found` rows skipped silently)

Configure via **Settings → Library → Hermeece URL**.

#### ntfy push notifications

Optional push notifications via [ntfy.sh](https://ntfy.sh) for
scan completions, MAM hits, library sync events, Hermeece sends,
and MAM cookie rotations. Per-event toggles, plus a daily/weekly
digest mode that batches all events into one consolidated
notification at 09:00 local time. Configure via
**Settings → Notifications**.

#### Encrypted credential store

MAM session tokens and Hardcover API keys are now stored
Fernet-encrypted in `athenascout_auth.db` instead of plaintext in
`settings.json`. The Fernet key is derived deterministically from
the same `auth_secret` already used for session signing. Existing
v1.0.x deployments auto-migrate plaintext credentials on first
v1.1.0 start. See [`SECURITY.md`](SECURITY.md#encrypted-credential-store-v110)
for the full design.

#### In-app log viewer

New **Logs** page (top-nav 📋 icon) shows the last 2000 log lines
from the running container with a search filter, color-coded
levels (red ERROR, amber WARNING, dim DEBUG), and an auto-scroll
toggle. Diagnose a stuck scan or misbehaving source without
`docker logs` from the host.

#### Other features

- **Omnibus detection** — compilations and box-sets that match a
  known series name are flagged with `is_omnibus=1` and rendered
  separately in series views so they don't shift series numbering.
- **Version SHA display** — the build's git SHA is baked into
  `/app/VERSION` at Docker build time and surfaced in
  Settings → Build SHA. The CI workflow passes
  `--build-arg GIT_SHA=${{ github.sha }}` automatically.
- **Per-page content widths** — data-heavy pages (Library,
  Authors, Author detail, Missing, Upcoming, MAM, Suggestions,
  Hidden, Database) now use a 1400px container; form/config
  pages stay at 1120px. The navbar stays narrow regardless.
- **Suggestion persistence fix** — the sidebar suggestion query
  now filters to `status='pending'` so dismissed/ignored
  suggestions stop reappearing.
- **Empty series cleanup** — orphaned series rows whose books
  were all filtered out are removed at the end of each scan.
- **Standalone vs series override** — when sources disagree on
  whether a title is standalone or part of a series, the merge
  layer respects the user's stored choice instead of overwriting.
- **Title → series matching** — books whose title contains a
  known series name get auto-linked in a post-scan pass.

### Changed

- **MAM threshold raised to 0.70** (was looser). Combined with
  the new scoring system this catches significantly fewer false
  positives. See `MATCH_PROMOTE_SCORE` in
  [`app/sources/mam.py`](app/sources/mam.py).
- **Audiobook filter strengthened** — title-based detection alone
  let some Audible editions through with clean titles. The filter
  now also checks JSON-LD `bookFormat` and list-page row text.
- **Source priority reorder** — MAM → Goodreads → Amazon →
  Hardcover → Kobo → IBDB → Google Books. Amazon promoted because
  it's the strongest signal for standalone vs series.
- **Frontend migrated to TypeScript.** The full migration shipped
  with permissive defaults (`strict: false`, `noImplicitAny: false`)
  so existing patterns survive; tightening can happen incrementally
  in v1.2. Build now runs `tsc -b && vite build`.

### Fixed

- **IBDB / Google Books / Amazon ID columns** could be missing on
  databases that hit a v40 → v41+ migration ordering bug. The
  startup column-existence safety net (`init_db` Step 3.5) now
  always ensures the columns exist regardless of the
  `user_version` counter.
- **Pen-name dedup not firing** in the merge pipeline — the
  `_merge_result` query previously only checked rows owned by the
  current author. Linked authors are now included via a
  `linked_author_ids` parameter so dedup works across identities.
- **Multi-select MAM scan progress** — the bulk endpoint was
  synchronous and never updated `state._mam_scan_progress`. Now
  runs as a supervised background task with the same widget hooks
  as the single-author scan.
- **Scroll preservation timing** — switched from
  `requestAnimationFrame` (fired before React layout completed)
  to `setTimeout(100ms)` so action handlers consistently restore
  scroll position.
- **Cookie rotation log spam** — every ~2s in-memory token update
  was logging at INFO. Demoted to DEBUG; only the persist-to-store
  step still logs at INFO.
- **Amazon false positives** — added author-validation check on
  search-card text so "Larroggio" and similar near-author
  collisions are rejected before the detail page is fetched.
- **Set detection regex** broadened to catch boxset/collection
  variants the previous version missed.
- **Google Books 429s** — the inherited retry wrapper was burning
  daily quota on rate-limit responses. Source now overrides
  `retries=0`.
- **Suggestion sidebar persistence** — only `status='pending'`
  suggestions are surfaced; ignored/dismissed entries no longer
  re-appear after a refresh.

### Security

- New encrypted credential store — see "Added" above and
  [`SECURITY.md`](SECURITY.md#encrypted-credential-store-v110).
- v1.0.x is no longer supported. Upgrade is drop-in compatible
  (no manual migration steps required).

### Internals (no user-visible change)

- New `app/digest.py` and `app/notify.py` digest queue.
- New `app/scoring.py` ported from Hermeece.
- New `app/secrets.py` Fernet store.
- New `app/log_buffer.py` ring buffer + `app/routers/logs.py`.
- New `app/routers/hermeece.py`.
- `app/lookup.py` orchestration refactor: `SourceSpec` registry +
  per-source `wait_for` timeouts + `PER_AUTHOR_BUDGET_SEC` cap.
- Frontend: new `src/types.ts` shared type registry; `tsconfig.json`
  + `tsconfig.node.json`; build script switched to
  `tsc -b && vite build`.

---

## [1.0.2] — 2026-03-something

- Real-time per-book scan progress
- Dashboard quick-nav additions
- CodeQL fixes

## [1.0.1] — 2026-03-something

- Per-book new_books progress fix

## [1.0.0] — 2026-03-something

- Initial public release
- Library-agnostic naming pass
- Source-scan progress fix
- Documentation polish + screenshots

(Pre-1.0 history lives in the git log; this changelog only covers
public releases.)

[1.1.2]: https://github.com/mnbaker117/AthenaScout/releases/tag/v1.1.2
[1.1.1]: https://github.com/mnbaker117/AthenaScout/releases/tag/v1.1.1
[1.1.0]: https://github.com/mnbaker117/AthenaScout/releases/tag/v1.1.0
[1.0.2]: https://github.com/mnbaker117/AthenaScout/releases/tag/v1.0.2
[1.0.1]: https://github.com/mnbaker117/AthenaScout/releases/tag/v1.0.1
[1.0.0]: https://github.com/mnbaker117/AthenaScout/releases/tag/v1.0.0
