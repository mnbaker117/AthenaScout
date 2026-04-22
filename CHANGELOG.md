# Changelog

All notable changes to AthenaScout are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.2.1] — 2026-04-22

Security-only patch release. No behavior changes.

### Security

- **Bumped `lxml` from `==5.2.0` to `>=6.1.0`** to close
  CVE-2026-41066 (GHSA-vfmq-68hx-4jfw). The lxml 6.0-and-earlier
  default configuration of `iterparse()` and `ETCompatXMLParser()`
  allowed XXE (XML external entity) resolution against local
  files. AthenaScout doesn't parse untrusted XML directly, but the
  scraper stack uses lxml transitively through BeautifulSoup's
  "lxml" parser when fetching source HTML — a compromised or
  malicious source response could otherwise have triggered local
  file disclosure during a scan. Patched in 6.1.0.

---

## [1.2.0] — 2026-04-15

Feature sprint closing out the v1.2 backlog. Two new source-
reliability features + TypeScript tightening (Tier 2 + partial
Tier 3). No runtime regressions — TS changes are build-time only.

### Added

- **Google Books circuit breaker.** After 5 consecutive 429
  responses from Google's Books API, the source auto-disables by
  flipping `google_books_enabled` to False and stamping
  `google_books_auto_disabled_at` in settings. A yellow Dashboard
  banner surfaces the auto-disable ("Google Books auto-disabled —
  API quota likely exhausted") with a link to Settings. The
  Settings page's Google Books toggle shows "Auto-disabled" as a
  distinct state from user-disabled, with a contextual description.
  Re-enabling in Settings clears the timestamp and rebuilds the
  source instance with a fresh counter. The anonymous Google Books
  API quota is low enough that most users will hit this on their
  first bulk scan — the circuit breaker prevents wasting ~15s of
  per-author scan budget on guaranteed-to-fail requests.

- **Goodreads resume-from-position retry.** Phase 2 of the
  source-timeout visibility feature (Phase 1 shipped in v1.1.9).
  When Goodreads times out mid-detail-loop, the source now
  preserves a `_partial_state` snapshot (books/series processed so
  far + the next-book index). After all other sources finish for
  that author, the orchestration layer checks if scan budget
  remains (≥30s) and retries Goodreads with `start_at=<index>`.
  The retry inherits the prior call's books/series snapshot so
  partial work from the first call isn't lost. On a second
  timeout, the log reports "processed N/M books total; ~X likely
  unscanned" so the user has a concrete number instead of just
  "Goodreads timed out." Sources that don't expose
  `_partial_state` are automatically skipped in the retry pass —
  adding resume support to another source later requires only
  implementing the same instance-attribute contract.

### Changed

- **TS Tier 2: inline subcomponent typing.** Replaced `({...}:any)`
  with proper interfaces on inline subcomponents across the five
  dense pages called out in the v1.2 TS backlog:
    * `BookViews.tsx`: `BookViewItemProps` + `BookViewListProps`
    * `SettingsPage.tsx`: `LangSelectProps`, `SFProps`, `STogProps`,
      `SSectionProps`
    * `AuthorDetailPage.tsx`: `SectionSharedProps` + `ISProps` /
      `SAProps` + `BookViewMode` union
    * `SetupWizard.tsx`: `SetupWizardProps`

- **TS Tier 3 partial: `strictNullChecks` enabled.** Full strict
  mode surfaced 176 errors — 114 were TS7006 implicit-any on
  callback params (`.map(l => ...)`, `(e) => ...`), which per the
  TS backlog memory's own guidance ("100% strict mode. The point
  of TS is leverage, not legalism") are not worth chasing.
  Compromise: enabled `strictNullChecks` only (the high-safety
  subset). 11 null-safety errors fixed across App.tsx,
  AuthorDetailPage, AuthorsPage, Dashboard, ImportExportPage, and
  MAMPage. `noImplicitAny` stays off — deferred permanently.

## [1.1.9] — 2026-04-15

Concurrent-scan reliability sprint. All reported symptoms came
from contention between the source-scan merge path and a MAM
scan holding the SQLite writer lock (plus two latent bugs that
only surfaced after the schema gap got closed).

### Added

- **Series "#X of Y" now counts mainline entries only.** Added
  a parallel `mainline_total` aggregate alongside `series_total`
  in the books/series/mam router SQL. The position label now
  reads "#4 of 4" for a mainline book in a 4-mainline + 1-prequel
  series instead of the confusing "#4 of 5"; the prequel reads
  "#0.5 of 4". `series_total` (which the Series browser uses for
  the "N books in the series" count) is unchanged — it still
  includes prequels and novellas. Frontend updated across
  `BookSidebar.tsx`, `BookViews.tsx` (grid + list views), and
  `types.ts`.

- **Bulk source-scan timeout summary.** `lookup_author` accepts an
  optional `timeout_collector` dict; `run_full_lookup` /
  `run_full_rescan` create one, pass it through, and log a
  per-source summary at end-of-scan ("Source 'goodreads' timed
  out for 3 authors — those authors may be under-scanned:
  Jay Aury, ..."). The counts also surface on the Dashboard scan
  widget as a yellow warning line when the scan completes, so a
  primary source silently losing the race on a chunk of the
  library doesn't disappear into debug-level logs. No retry — just
  visibility. The retry-from-position feature is deferred to v1.2.

- **Top navbar widened from 1120px to 1280px.** Fits a 4-digit
  Suggestions badge count without triggering the nav-items row's
  horizontal scroll. No layout shift in the main content area.

### Fixed

- **`no such column: ibdb_id` on every single-author scan.** Not
  a migration-reordering regression — the `authors` table was
  genuinely missing `ibdb_id` and `google_books_id` columns. When
  those sources were added in Sprint 4 the columns landed on
  `books` but never on `authors`. `lookup.py`'s UPDATE authors
  SET `{source}_id=?` pattern crashed for ibdb every time; the
  google_books path would have done the same but was rate-limited
  out before it got to write. Fix: added both to the authors
  SCHEMA block for fresh installs, appended two ALTER migrations,
  and extended the `_ensure_columns` startup safety net so
  DBs that somehow miss both auto-repair on next boot.

- **ibdb field-name mappings were wrong against the live API.**
  The source was written for a pre-2026 `ibdb.dev` response shape
  and expected snake_case keys (`isbn_13`, `publication_date`)
  plus a bare URL string for `cover`/`image`/`thumbnail`. Live API
  now returns camelCase (`isbn13`, `synopsis`, `publicationDate`)
  and `image` as a *dict* `{id, url, width, height}`. So ibdb was
  shoving a dict into `BookResult.cover_url`, which then crashed
  sqlite3 parameter binding with `type 'dict' is not supported`.
  Pre-1.1.9 the missing-`ibdb_id`-column error masked this entirely
  (the SQL bombed before reaching the per-book path); closing the
  schema gap unmasked it. Fix: prefer camelCase keys with snake_case
  as fallback, extract `image.url` when image is a dict, and
  type-guard `description`/`page_count`/`language` against
  dict/unexpected values landing in scalar slots. After the fix
  ibdb contributes real metadata — verified merging 11 updated
  books for a test author where previously it merged zero.

- **Concurrent MAM scan starved source-scan merges of the writer
  lock.** Observed in dev2 and dev3 testing: a MAM full scan
  would hit `UPDATE books` in its per-book loop and hold the
  SQLite single-writer lock long enough that a source scan's
  subsequent `UPDATE authors` would sit through the whole 30-second
  `busy_timeout` and die with `database is locked`. Amazon, then
  Goodreads, then Hardcover all lost entire per-author merges
  this way. The fix landed in three layers:

  1. **MAM pauses when a source scan is active.** New
     `state._source_scan_refs: int` counter, incremented in
     `lookup_author`'s entry and decremented in its `finally` so
     exceptions/cancellations can't leave MAM stranded. MAM's
     per-book loop checks the counter before each `check_book`
     HTTP+UPDATE cycle; if non-zero, it commits any pending
     transaction and sleeps 1s until the counter drops to 0. A
     20-minute safety cap guards against a stuck refcount.
  2. **MAM commits its transaction before the pause-sleep.** Without
     this, MAM's mid-batch UPDATE sits in an open implicit
     transaction holding the writer lock while MAM sleeps, which
     re-creates the exact starvation bug we were trying to prevent.
     Verified in logs: with the commit-before-pause, Goodreads'
     UPDATE authors lands in ~40ms instead of waiting the full 30s.
  3. **Retry-on-lock for the final author-stamp UPDATE.** The
     `UPDATE authors SET verified=1, last_lookup_at=?` at the end
     of `lookup_author` opens a fresh connection just for that one
     write, so the refcount pause doesn't help there. Wrapped it
     in a 5-attempt exponential-backoff retry (1/2/4/8s); on
     persistent lock we log a WARNING and let the scan complete —
     the per-source merges already committed and the next scheduled
     lookup will re-attempt the stamp.

- **Scheduled MAM scan couldn't be cancelled from the UI.** The
  scheduled scan runs inline inside the long-lived `_mam_scheduler`
  task (via `state.supervised_task`), not as a discrete task, so
  `_mam_scan_task.cancel()` had nothing to target. Clicking Stop
  silently did nothing and the `/mam/scan/cancel` fallback branch
  returned `{"message": "No MAM scan running"}` — which the
  frontend toast dressed up as a success message. Fix:
  `state._scheduled_mam_cancel_requested` flag, reset by the
  scheduler on each iteration, set by `/mam/scan/cancel` when the
  active scan is `type=scheduled`, read via a `cancel_check`
  closure that the scheduler now passes to `mam_scan_batch`.
  Cancel latency: ≤2s (the per-book HTTP round-trip boundary).
  The cancelled scan also writes `status='cancelled'` to
  `sync_log` instead of "complete" and suppresses the "scan
  complete" ntfy push, so you don't get a false "done!" push
  right after clicking Stop.

## [1.1.8] — 2026-04-14

### Fixed

- **`mam_category` migration still didn't run after v1.1.7.** The
  v1.1.7 fix removed the misplaced middle entry AND appended a new
  one — net zero length change. Upgraded DBs from v1.1.5 already
  had `user_version=45`, and `target_version = len(MIGRATIONS)`
  was also 45, so the runner's `if current_version < target_version`
  short-circuit skipped everything. The column never got added;
  MAM scans kept crashing with `no such column: mam_category`.

  Proper fix: restore the middle entry as a deliberate no-op AND
  keep the appended entry. The duplicate-ALTER error handler
  already tolerates "duplicate column" / "already exists" (line
  621 of database.py), so running the same statement twice on a
  fresh DB is silent. On upgraded DBs the middle slot is already
  past their user_version (skipped) and only the appended entry
  runs, which adds the column.

  Added a stern comment on the middle slot warning future me that
  removing it re-breaks upgrades by shifting every downstream
  index by one.

## [1.1.7] — 2026-04-14

### Fixed

- **`mam_category` column never got added on upgraded databases.**
  v1.1.5 put the `ALTER TABLE books ADD COLUMN mam_category` entry
  inline with the other `mam_*` migrations in the middle of the
  list. The migration runner keys on `PRAGMA user_version`, which
  stores the count of entries already applied — so inserting an
  entry at index ~28 meant it fell below every existing DB's
  user_version (44+) and was silently skipped. First MAM scan
  after a v1.1.5 upgrade crashed with `no such column:
  mam_category`.

  Fix: moved the migration to the end of the list where the docs
  (and the existing "Sprint 4 initially placed ibdb_id before
  pen_name_links" apology comment on v40 migrations) say it
  belongs. Added a comment on the new entry calling out the
  append-only contract so the next migration doesn't repeat the
  bug.

## [1.1.6] — 2026-04-14

### Fixed

- **Calibre sync created a duplicate row instead of merging with
  the existing Missing entry.** When a Missing book got fulfilled
  (Send-to-Hermeece → CWA → Calibre → AS sync), the sync inserted
  a fresh Calibre-sourced row alongside the original discovery
  row. The follow-up "ownership flip" pass then set `owned=1` on
  the Missing row, leaving two `owned=1` rows for the same book —
  one with the source-scrape provenance + MAM linkage but no
  series / tags / Calibre identity, and one with the Calibre data
  but missing the MAM history. Symptom the user reported: AS
  showed two entries for "Turncoat's Truth" (id 12852 + 12911)
  after a Hermeece-fulfilled Missing book synced back.

  Root cause was at [calibre_sync.py:326](app/calibre_sync.py#L326):
  the existence check looked up by `calibre_id = ? AND source =
  'calibre'`, so any row from a non-Calibre source (`goodreads`,
  `hardcover`, etc.) without a `calibre_id` was invisible to the
  lookup → INSERT path always taken. Fix: when the calibre_id
  lookup misses, fall back to a same-author + title-match
  candidate search (with article-stripping for "The X" / "X" —
  same shape as the ownership-flip pass below). On exactly one
  match, MERGE: attach `calibre_id` + `source='calibre'` + apply
  the latest Calibre fields to the existing discovery row,
  preserving its `mam_*` columns and source-scrape provenance.
  Ambiguous matches (multiple candidates) still fall through to
  INSERT — better to leave a known dup than risk merging into the
  wrong row.

  Pre-existing duplicates from this bug aren't auto-merged. To
  reconcile a known pair manually:
  ```sql
  -- pick keep_id = the discovery row (has mam_status / mam_torrent_id)
  -- pick drop_id = the calibre-sourced row (has calibre_id / series_id)
  UPDATE books SET
    calibre_id = (SELECT calibre_id FROM books WHERE id=:drop_id),
    source = 'calibre',
    series_id = (SELECT series_id FROM books WHERE id=:drop_id),
    series_index = (SELECT series_index FROM books WHERE id=:drop_id),
    isbn = COALESCE((SELECT isbn FROM books WHERE id=:drop_id), isbn),
    cover_path = (SELECT cover_path FROM books WHERE id=:drop_id),
    description = COALESCE((SELECT description FROM books WHERE id=:drop_id), description),
    tags = (SELECT tags FROM books WHERE id=:drop_id),
    rating = (SELECT rating FROM books WHERE id=:drop_id),
    language = (SELECT language FROM books WHERE id=:drop_id),
    publisher = (SELECT publisher FROM books WHERE id=:drop_id),
    formats = (SELECT formats FROM books WHERE id=:drop_id)
  WHERE id = :keep_id;
  DELETE FROM books WHERE id = :drop_id;
  ```

## [1.1.5] — 2026-04-14

### Added

- **Capture MAM category during scan + forward to Hermeece.**
  New `books.mam_category` column populated by `check_book`
  from the item dict MAM's search API already returns (values
  like "Ebooks - Fantasy"). Send-to-Hermeece includes it in
  `GrabItem.category` so the Hermeece grab row + dashboard no
  longer inherit an empty category for AthenaScout-originated
  grabs. Pre-migration rows (scanned before v1.1.5) send an
  empty string — Hermeece v1.2.2 tolerates that fallback.

## [1.1.4] — 2026-04-14

### Fixed

- **Send-to-Hermeece omitted the book title.** The payload built
  in `app/routers/hermeece.py` was only sending `url_or_id` and
  `author`, so Hermeece fell back to a `manual_inject_<id>`
  placeholder for the torrent name. That placeholder landed on the
  grab row's `torrent_name` and downstream everything (dashboard
  widgets, review queue label, metadata enricher's fuzzy search)
  used the garbage string. Now includes `title` from the book row.
  Pairs with Hermeece v1.1.4 which accepts the field.

## [1.1.3] — 2026-04-14

### Added

- **Hermeece shared API key.** New "Hermeece API Key" field in
  Settings → Library → Hermeece Integration, stored Fernet-encrypted
  in the auth DB alongside the MAM session ID and Hardcover token.
  "Send to Hermeece" now sends the token as `X-API-Key` on every
  batch POST. Matches the new `athenascout_api_key` credential in
  Hermeece v1.1.1 (auth middleware accepts the header as an
  alternative to the session cookie). Sends with a missing or
  unconfigured key fail fast with an actionable 400 pointing the
  user at Hermeece's Credentials page.

### Fixed

- **"Send to Hermeece" silently broken against v1.1.0 Hermeece.**
  Hermeece's v1.1 added a session-cookie auth middleware that
  rejected AthenaScout's cookieless POSTs with `HTTP 401:
  Authentication required`. AthenaScout's hermeece router comment
  still assumed the pre-auth "same LAN, same auth boundary"
  invariant. The new shared API key restores the handoff.

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
