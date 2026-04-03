# Publishing AthenaScout — GHCR + Unraid Community Apps Guide

## Part 1: Publish to GitHub Container Registry (GHCR)

### Automatic (GitHub Actions — Recommended)

The repo includes a GitHub Actions workflow (`.github/workflows/docker-publish.yml`) that automatically builds and pushes the Docker image to GHCR on every push to `main`.

**One-time setup:**

1. Go to your repo: https://github.com/mnbaker117/AthenaScout
2. Navigate to **Settings → Actions → General**
3. Under "Workflow permissions", select **"Read and write permissions"**
4. Click Save

That's it. Every push to `main` will now:
- Build the Docker image
- Push it to `ghcr.io/mnbaker117/athenascout:latest`
- Also tag with the commit SHA

**To create a versioned release:**
```bash
git tag v1.0.0
git push origin v1.0.0
```
This creates additional tags: `ghcr.io/mnbaker117/athenascout:1.0.0` and `:1.0`

### Manual Push (from your Unraid server)

```bash
# Log in to GHCR (use a Personal Access Token with packages:write scope)
echo "YOUR_PAT_TOKEN" | docker login ghcr.io -u mnbaker117 --password-stdin

# Build and push
cd /mnt/user/appdata/athena-scout
./build.sh --push
```

### Make the Package Public

By default, GHCR packages are private. To make it public:

1. Go to https://github.com/mnbaker117?tab=packages
2. Click on "athenascout"
3. Click **Package settings** (right sidebar)
4. Scroll to "Danger Zone" → **Change visibility** → Public

---

## Part 2: Add as Unraid Docker Container (without Compose)

Once the image is on GHCR, anyone can add it directly via Unraid's "Add Container" form:

| Field | Value |
|---|---|
| **Name** | AthenaScout |
| **Repository** | `ghcr.io/mnbaker117/athenascout:latest` |
| **Network Type** | Bridge |
| **WebUI** | `http://[IP]:[PORT:8787]` |

Then add these via "Add another Path, Port, Variable":

**Port:**
| Container Port | Host Port | Description |
|---|---|---|
| 8787/tcp | 8787 | Web UI |

**Paths:**
| Container Path | Host Path | Mode | Description |
|---|---|---|---|
| `/calibre` | `/mnt/user/appdata/calibre/Calibre Library` | Read Only | Calibre library |
| `/app/data` | `/mnt/user/appdata/athena-scout` | Read/Write | App database |

**Variables (Add as needed):**
| Key | Value | Description |
|---|---|---|
| `CALIBRE_DB_PATH` | `/calibre/metadata.db` | Path to metadata.db |
| `CALIBRE_LIBRARY_PATH` | `/calibre` | Library root |
| `HARDCOVER_API_KEY` | `Bearer eyJ...` | Hardcover API key |
| `CALIBRE_WEB_URL` | `http://IP:8083` | Calibre-Web URL |
| `CALIBRE_URL` | `https://IP:8181` | Calibre server URL |

Alternatively, use the XML template for automatic setup — see Part 3.

---

## Part 3: Unraid XML Template (Add Container with Pre-filled Fields)

The `unraid-template.xml` file pre-populates the Add Container form. To use it:

### For Yourself (Manual Template Install)

1. SSH into Unraid
2. Copy the template:
   ```bash
   mkdir -p /boot/config/plugins/dockerMan/templates-user
   cp /mnt/user/appdata/athena-scout/unraid-template.xml \
      /boot/config/plugins/dockerMan/templates-user/my-AthenaScout.xml
   ```
3. In Unraid Docker tab → Add Container → Template dropdown → select "AthenaScout"
4. Fill in your paths and API keys → Apply

### For Others (Template Repository)

To let other Unraid users find your app via the template dropdown:

1. Create a new GitHub repo: `mnbaker117/unraid-templates`
2. Add the XML template there with the correct path structure:
   ```
   unraid-templates/
   └── templates/
       └── AthenaScout.xml
   ```
3. Other users add your template repo URL in:
   **Unraid → Docker → Template Repositories** (at the bottom of the page)
   ```
   https://github.com/mnbaker117/unraid-templates
   ```
4. They can then find AthenaScout in their Add Container template dropdown

---

## Part 4: Submit to Unraid Community Apps (CA)

Community Apps is the main app store for Unraid, managed by Squidly271.

### Prerequisites

- Your Docker image must be publicly available on a registry (GHCR, Docker Hub)
- You need a template repository (see Part 3 above)
- The template XML must be well-formed and tested

### Submission Steps

1. **Set up your template repository** (Part 3 above)

2. **Fork the Community Apps repo:**
   https://github.com/Squidly271/community.applications

3. **Add your template repo** to the list:
   - Edit the file `sources/templates/templates.json`
   - Add your entry:
     ```json
     {
       "name": "mnbaker117's Repository",
       "url": "https://github.com/mnbaker117/unraid-templates"
     }
     ```

4. **Submit a Pull Request** to Squidly271/community.applications
   - Title: "Add AthenaScout — Book Library Completionist Tracker"
   - Description: Brief overview of what the app does, link to your repo

5. **Wait for review** — Squidly271 or a moderator reviews and merges

### Tips for Successful Submission

- Make sure your template XML validates (no broken tags)
- Include a working icon URL (the raw GitHub URL to your icon.svg)
- Test that the image pulls and runs correctly from a clean Unraid install
- Have a clear README in your main repo
- Respond promptly to any review feedback
- The app should be stable and useful — CA maintainers reject low-quality submissions

### Alternative: Unraid Forums

You can also share your app directly on the Unraid forums:
- Post in the **Docker Containers** subforum
- Include your template repo URL and setup instructions
- Users can add your template repo manually

---

## Part 5: Docker Hub (Optional Alternative to GHCR)

If you prefer Docker Hub over GHCR:

1. Create account at https://hub.docker.com
2. Create repository: `mnbaker117/athenascout`
3. Update the GitHub Actions workflow — change `REGISTRY` to `docker.io`
4. Add Docker Hub credentials as GitHub Secrets:
   - `DOCKERHUB_USERNAME`
   - `DOCKERHUB_TOKEN`
5. Update template XML `<Repository>` to `mnbaker117/athenascout:latest`

GHCR is recommended since it's free for public repos and lives alongside your code.
