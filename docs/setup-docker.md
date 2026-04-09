# Docker Setup

The official image is published at `ghcr.io/mnbaker117/athenascout`.
Use `:latest` for the stable release or `:main` to track the active
development branch.

## Minimum compose file

```yaml
services:
  athenascout:
    image: ghcr.io/mnbaker117/athenascout:latest
    container_name: athenascout
    ports:
      - "8787:8787"
    volumes:
      # Your Calibre library, mounted read-only.
      # AthenaScout never writes to your Calibre database.
      - /path/to/your/calibre:/calibre:ro
      # AthenaScout's persistent data: per-library SQLite databases,
      # settings.json, the auth secret, and the auth user database.
      - /path/to/athenascout/data:/app/data
    restart: unless-stopped
```

That's the entire required configuration. After starting the
container, open `http://your-server:8787` and follow the
[first-run walkthrough](first-run.md).

> 💡 **Container runs as a non-root user (UID 1000).** If you have an
> existing data directory created by an older root-run container, run
> `sudo chown -R 1000:1000 /path/to/athenascout/data` on the host once
> before starting. Fresh deployments need no action — the directory is
> created with the right ownership automatically.

---

## Volume mounts explained

| Mount | Purpose | Required |
|---|---|---|
| `/calibre` | Your Calibre library directory (a folder containing one or more `metadata.db` files, one level deep) | Yes — unless you set `CALIBRE_PATH` to point elsewhere |
| `/app/data` | AthenaScout's persistent data: per-library SQLite databases, `settings.json`, `auth_secret`, the auth users database, scan logs | Yes — without it, your admin account, library data, and scan history are lost on container restart |

The `:ro` (read-only) flag on the Calibre mount is recommended.
AthenaScout has no code path that writes to your Calibre database, but
there's no reason not to enforce that at the mount level too.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CALIBRE_PATH` | `/calibre` | Discovery root. AthenaScout walks this directory and registers every immediate subdirectory containing a `metadata.db` as a separate library. |
| `CALIBRE_EXTRA_PATHS` | *(empty)* | Comma-separated list of **container-side** paths to additional Calibre libraries beyond the discovery root. See [Multiple libraries](#multiple-libraries) below. |
| `WEBUI_PORT` | `8787` | Port the app listens on inside the container. |
| `SYNC_INTERVAL_MINUTES` | `60` | How often the scheduler re-syncs the active library backend. Set to `0` to disable scheduled sync (manual still works). |
| `LOOKUP_INTERVAL_MINUTES` | `4320` | How often the scheduler runs source scans across all authors. `4320` is 3 days. Set to `0` to disable. |
| `MAM_SCAN_INTERVAL_MINUTES` | `360` | How often the scheduler runs MAM availability scans. Only used when MAM is enabled. |
| `HARDCOVER_API_KEY` | *(empty)* | Optional. Seeds the Hardcover API key on first run. You can also paste it in Settings later. |
| `CALIBRE_WEB_URL` | *(empty)* | Optional. If you run [Calibre-Web](https://github.com/janeczku/calibre-web), set this to its base URL and AthenaScout will turn each owned book into a clickable deep link. |
| `VERBOSE_LOGGING` | `false` | Set to `true` for per-book scan decisions in the container logs. |
| `TZ` | `UTC` | Timezone for scheduled scan timestamps. |

`HARDCOVER_API_KEY` and `MAM_SESSION_ID` are deliberately NOT pre-set
in the Dockerfile. Pass them via Compose `environment:`, an env file,
or Docker secrets — whatever fits your deployment style.

> 💡 **Why are the env vars named `CALIBRE_*`?** Calibre is currently
> the only library backend AthenaScout supports, so the env vars are
> named after it. The internal architecture is set up to *eventually*
> accept additional backends — see the
> [`LibraryApp` interface](../app/library_apps/base.py) and the
> contributor section in the README for the candidate list — and
> when those land they'll get their own `*_PATH` / `*_EXTRA_PATHS`
> env vars without breaking the existing Calibre ones.

---

## Multiple libraries

AthenaScout has first-class multi-library support. There are two ways
to set it up, and you can mix them.

### Option A: discovery root (simplest)

If all your Calibre libraries live under a single parent directory,
mount that parent at `/calibre` and let AthenaScout discover them
automatically:

```yaml
    volumes:
      # /mnt/user/appdata/calibre/ contains:
      #   /mnt/user/appdata/calibre/fiction/metadata.db
      #   /mnt/user/appdata/calibre/non-fiction/metadata.db
      #   /mnt/user/appdata/calibre/audiobooks/metadata.db
      - /mnt/user/appdata/calibre:/calibre:ro
      - /mnt/user/appdata/athenascout:/app/data
```

That's it. Each subdirectory containing a `metadata.db` shows up as a
separate library in the dashboard's library switcher. No env vars
needed.

### Option B: `CALIBRE_EXTRA_PATHS`

When your libraries live in totally different places on the host (or
on different drives), use `CALIBRE_EXTRA_PATHS` to register each one
individually.

> ⚠️ **The most common mistake here is using HOST paths instead of
> CONTAINER paths.** Read this carefully.

Each path you list **must** be a path *inside the container*, and each
one **must** have a matching Docker volume mount.

Worked example — say you have:

- `/mnt/user/appdata/calibre/books` (your main fiction library)
- `/srv/audiobooks/calibre` (a separate audiobook library on a different drive)

Your compose file should look like:

```yaml
services:
  athenascout:
    image: ghcr.io/mnbaker117/athenascout:latest
    container_name: athenascout
    ports:
      - "8787:8787"
    volumes:
      # Primary library — host path on the left, container path on the right.
      - /mnt/user/appdata/calibre/books:/calibre:ro
      # Extra library — host path on the left, container path on the right.
      - /srv/audiobooks/calibre:/calibre-audio:ro
      # Persistent data
      - /mnt/user/appdata/athenascout:/app/data
    environment:
      # The value here is the CONTAINER path (right side of the mount),
      # NOT the host path. /calibre-audio, NOT /srv/audiobooks/calibre.
      - CALIBRE_EXTRA_PATHS=/calibre-audio
    restart: unless-stopped
```

Inside the container, `/srv/audiobooks/calibre` does not exist — that
path is meaningful only on the host. The container only sees
`/calibre-audio`, the path you exposed it as. If you put the host path
in `CALIBRE_EXTRA_PATHS`, AthenaScout starts, fails to find anything
at that path, logs a warning, and silently moves on. There's no error
in the UI — just a missing library on the dashboard.

### Multiple extra paths

Comma-separate them. Each one needs its own volume mount:

```yaml
    volumes:
      - /mnt/user/appdata/calibre/books:/calibre:ro
      - /srv/audiobooks/calibre:/calibre-audio:ro
      - /mnt/scratch/manga/calibre:/calibre-manga:ro
      - /mnt/user/appdata/athenascout:/app/data
    environment:
      - CALIBRE_EXTRA_PATHS=/calibre-audio,/calibre-manga
```

---

## Unraid

AthenaScout works well with the Unraid Docker Compose Manager and the
standard Community Apps template format. Use the values above (image,
port, volume mounts, env vars) when filling out the template fields.

Recommended Unraid paths:

- Calibre library: `/mnt/user/appdata/calibre/` (or wherever your
  Calibre installation lives)
- AthenaScout data: `/mnt/user/appdata/athenascout/`

Most Unraid users run AthenaScout alongside the official Calibre
Docker container. Both can read the same library directory
simultaneously since AthenaScout's mount is read-only.

---

## Updating

```bash
docker compose pull
docker compose up -d
```

AthenaScout uses SQLite schema versioning, so updates apply migrations
automatically on first start of the new image. Your data, settings,
and admin account survive updates.

There's also an idempotent series-row dedupe pass that runs on every
startup — it stays inert on healthy databases and will quietly clean
up any normalized-name duplicates if they ever appear.

---

## Health check

The image ships with a built-in healthcheck that hits `/api/health`
every 2 minutes. `docker ps` and most container monitoring tools will
pick it up automatically:

```
NAMES         STATUS
athenascout   Up 4 hours (healthy)
```

---

## Troubleshooting

**The container starts but the dashboard shows "no libraries found".**
Check `docker logs athenascout`. The most common causes:
- A `CALIBRE_EXTRA_PATHS` value that uses a host path instead of a
  container path — see [Multiple libraries](#multiple-libraries)
  above.
- A volume mount that points at an empty directory or a directory
  that doesn't actually contain a `metadata.db`.
- A Calibre library directory that's owned by a UID the container
  user (1000) can't read.

**I can reach the port but I see a login screen and I haven't created
an account.**
That's the first-run wizard. Click through and create your admin
account. See the [first-run walkthrough](first-run.md).

**I forgot my admin password.**
Reset by deleting the auth users database from inside the data
directory. Existing library data is preserved — only the auth row is
wiped:

```bash
docker exec -it athenascout sh -c 'rm /app/data/athenascout_auth.db'
docker restart athenascout
```

You'll be prompted to create a new admin account on next page load.

**Calibre updates aren't showing up in AthenaScout.**
AthenaScout uses mtime checking on `metadata.db` to skip libraries
that haven't changed since the last sync. If a sync seems stale,
trigger a manual one from the dashboard ("Sync Library" button) — it
ignores the mtime cache.

**Permission denied on `/app/data` after upgrade.**
You're running an old data directory that was created by a root
container. The current image runs as UID 1000 for security. Fix once
on the host:

```bash
sudo chown -R 1000:1000 /path/to/your/athenascout/data
```
