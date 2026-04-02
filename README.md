# AthenaScout

**A self-hosted book library completionist tracker.** AthenaScout syncs with your Calibre library and cross-references metadata sources like Goodreads, Hardcover, and Kobo to find books you're missing from authors and series you already collect.

Built for readers who want to know: *"What am I missing?"*

---

## Features

**Library Sync**
- Reads your Calibre `metadata.db` directly (read-only mount) — imports authors, series, books, covers, tags, and all metadata
- Automatic re-sync on a configurable interval
- Full metadata including ratings, descriptions, page counts, ISBNs, formats, and publishers

**Multi-Source Discovery**
- **Goodreads** (Primary) — Web scraping with two-pass author/book resolution. Series detection, publication dates, language filtering
- **Hardcover** — GraphQL API with `book_series` relation for accurate series data. Requires free API key
- **Kobo** — Web scraping for ebook availability
- **FantasticFiction** — Web scraping for genre fiction (currently limited by Cloudflare)
- Source priority system: Goodreads data always wins conflicts with lower-priority sources

**Smart Scanning**
- Regular scans skip already-known books (URL backfill only) for speed
- Full Re-Scan mode visits every book page to refresh all metadata
- Per-author re-scan from author detail pages
- Configurable rate limits per source to avoid blocks

**Import & Export**
- Import books from Goodreads or Hardcover URLs (single or batch)
- Import preview with duplicate detection and fuzzy matching
- Series selection dropdown when multiple series options exist
- Export to CSV or text with filter options (All, Library, Missing)
- Copy-to-clipboard support

**Series Intelligence**
- Multi-author series support (shared series across different authors)
- Series position display (#X of Y) on cards, list view, and sidebar
- Source priority for series data — Goodreads overwrites lower-priority sources
- Hardcover series scoring heuristic picks most specific series over broad franchise tags

**Library Management**
- Three themes: Dark, Dim, Light
- Grid and List view modes
- Group by Author or Series
- Universal search across title, author, and series
- Sort by title, author, date, or date added
- Dismiss, Hide, or Delete discovered books
- Edit book metadata (dates, ISBN, series position, description)
- New book badges with bulk dismiss

**Integrations**
- Calibre-Web deep links — click to open any owned book directly in Calibre-Web
- Calibre Library quick-access button on dashboard
- Clickable metadata source badges (Goodreads, Hardcover, Kobo) linking to each book's source page

**Mobile Responsive**
- Touch-friendly interface with proper tap targets
- Responsive grid layout, full-screen sidebar on mobile
- Sticky search/filter controls
- Smooth slide and fade animations

---

## Quick Start

### Option A: Pre-built Image (Recommended)

```bash
# 1. Clone or download
git clone https://github.com/mnbaker117/athena-scout.git
cd athena-scout

# 2. Build the Docker image
./build.sh

# 3. Edit docker-compose.yml — update the Calibre library path
nano docker-compose.yml

# 4. Start
docker-compose up -d
```

### Option B: Build Inline

```bash
# In docker-compose.yml, comment out 'image:' and uncomment 'build:'
docker-compose up -d --build
```

### Option C: Unraid (Docker Compose Manager)

1. SSH into your Unraid server
2. Extract and build the image:
   ```bash
   cd /mnt/user/appdata
   tar xzf athena-scout-v12.tar.gz
   cd athena-scout
   ./build.sh
   ```
3. In Unraid's Docker Compose Manager, create a new stack
4. Paste the contents of `docker-compose.yml` into the stack editor
5. Update the Calibre library path and any environment variables
6. Deploy the stack

Open **http://your-server:8787** and you're ready to go.

---

## Configuration

### Docker Compose Environment Variables

```yaml
environment:
  # ── Calibre Library ──────────────────────────────────────
  - CALIBRE_DB_PATH=/calibre/metadata.db
  - CALIBRE_LIBRARY_PATH=/calibre

  # ── Calibre Integration (optional) ──────────────────────
  - CALIBRE_WEB_URL=http://192.168.1.100:8083    # Calibre-Web deep book links
  - CALIBRE_URL=https://192.168.1.100:8181        # Calibre content server

  # ── Metadata Sources ────────────────────────────────────
  - HARDCOVER_API_KEY=Bearer eyJ...               # From hardcover.app → Account → API

  # ── Sync Intervals ─────────────────────────────────────
  - SYNC_INTERVAL_MINUTES=60                      # Calibre re-sync (0 = disabled)
  - LOOKUP_INTERVAL_MINUTES=4320                  # Source scan interval (0 = disabled)

  # ── App Settings ───────────────────────────────────────
  - WEBUI_PORT=8787                               # Web UI port
  - VERBOSE_LOGGING=false                         # Debug logging
```

Environment variables seed settings on first launch. After that, the Settings page in the web UI is the source of truth.

### Volumes

| Mount | Purpose |
|---|---|
| `/path/to/calibre:/calibre:ro` | Your Calibre library (read-only) |
| `./data:/app/data` | App database and settings (persists across restarts) |

### Data Persistence

The `./data` directory contains:
- `athenascout.db` — SQLite database with all books, authors, series, and sync history
- `settings.json` — User preferences and API keys

This directory survives container restarts and image updates. Back it up if you value your data.

---

## Source Setup

| Source | Auth | Priority | Notes |
|---|---|---|---|
| **Goodreads** | None | 1 (Primary) | Two-pass scraping: author list page → individual book pages. Most reliable for series data and dates |
| **Hardcover** | API key (free) | 2 | GraphQL API with `book_series` relation. Get key from [hardcover.app](https://hardcover.app) → Account → API. Include `Bearer ` prefix |
| **Kobo** | None | 3 | Web scraping. Results may be incomplete for some authors |
| **FantasticFiction** | None | 4 | Currently blocked by Cloudflare. Disabled by default |

Sources are checked in priority order. When the same book is found across multiple sources, Goodreads metadata wins. Source URLs from all sources are preserved as clickable badges.

---

## Architecture

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, aiosqlite |
| Frontend | React 18, Vite, inline styles |
| Database | SQLite |
| Container | Docker multi-stage build (Node → Python) |
| Sources | Modular plugin system in `app/sources/` |

### Key Files

```
athena-scout/
├── app/
│   ├── main.py              # FastAPI routes and API endpoints
│   ├── config.py             # Settings, env vars, defaults
│   ├── database.py           # SQLite schema and migrations
│   ├── calibre_sync.py       # Calibre metadata.db reader
│   ├── lookup.py             # Multi-source merge engine
│   └── sources/
│       ├── base.py           # BookResult/SeriesResult models
│       ├── goodreads.py      # Goodreads scraper
│       ├── hardcover.py      # Hardcover GraphQL client
│       ├── kobo.py           # Kobo scraper
│       └── fantasticfiction.py
├── frontend/
│   ├── src/App.jsx           # Single-file React SPA
│   ├── index.html
│   └── public/icon.svg       # App icon
├── docker-compose.yml
├── Dockerfile
├── build.sh
└── requirements.txt
```

---

## Troubleshooting

**Books not appearing after scan**
- Enable Verbose Logging in Settings to see per-book decisions
- Check Docker logs: `docker logs athena-scout`
- Common reasons: foreign language filter, set/collection detection, contributor-only credit

**Goodreads returning no results**
- Goodreads rate-limits aggressively. Increase the Goodreads rate limit in Settings (default: 2 seconds)
- The author name must closely match what Goodreads uses

**Hardcover API errors**
- Ensure the API key includes the `Bearer ` prefix (with space)
- Keys expire — regenerate at hardcover.app if needed

**Series not matching across sources**
- Run a Full Re-Scan to let the source priority system reconcile series data
- Goodreads series always takes priority over Hardcover/Kobo

**Database migration on upgrade**
- The app automatically renames `librarian.db` → `athenascout.db` on first run after upgrading from Calibre Librarian

---

## Development

```bash
# Backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8787

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

---

## License

MIT — see [LICENSE](LICENSE) for details.
