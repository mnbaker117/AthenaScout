# Standalone Setup

AthenaScout ships PyInstaller-built native binaries for Linux,
Windows, and macOS as an alternative to the Docker image. Downloads
are on the [GitHub releases page](https://github.com/mnbaker117/AthenaScout/releases).

The standalone build is functionally identical to the Docker image —
same Python code, same React frontend, same database schema. Only the
install steps and the default data directory differ.

## Should I use Docker or standalone?

| Use Docker if | Use standalone if |
|---|---|
| You already run Docker / Unraid / a NAS | You're on a desktop and don't want a Docker daemon |
| You want one-line updates (`docker compose pull`) | You're running on the same machine as Calibre Desktop |
| You want isolation from your host system | You're testing AthenaScout locally before deciding |
| You want the official healthcheck and labels | You don't need any of that |

Docker is the recommended path for long-term self-hosting. Standalone
is ideal if you're trying it out, running it next to a desktop Calibre
install, or just don't want a container runtime in your life.

---

## Linux

1. Download `athenascout-linux-x86_64` from the
   [latest release](https://github.com/mnbaker117/AthenaScout/releases).
2. Make it executable:
   ```bash
   chmod +x athenascout-linux-x86_64
   ```
3. Run it:
   ```bash
   ./athenascout-linux-x86_64
   ```
4. Open `http://localhost:8787` in your browser.

### Data directory

AthenaScout follows the XDG base directory spec on Linux. Default
location:

```
~/.local/share/athenascout/
```

Override with the `DATA_DIR` environment variable if you want to put
it somewhere else (a system-wide location, a different drive, etc.):

```bash
DATA_DIR=/srv/athenascout ./athenascout-linux-x86_64
```

### Running as a systemd service

Create `/etc/systemd/system/athenascout.service`:

```ini
[Unit]
Description=AthenaScout
After=network.target

[Service]
Type=simple
User=youruser
ExecStart=/opt/athenascout/athenascout-linux-x86_64
Restart=on-failure
Environment=DATA_DIR=/var/lib/athenascout

[Install]
WantedBy=multi-user.target
```

Then enable and start:

```bash
sudo systemctl enable --now athenascout
journalctl -u athenascout -f    # follow logs
```

---

## Windows

1. Download `athenascout-windows-x86_64.exe` from the
   [latest release](https://github.com/mnbaker117/AthenaScout/releases).
2. Move it somewhere stable, e.g. `C:\Program Files\AthenaScout\`.
3. Double-click to run, or launch from PowerShell:
   ```powershell
   .\athenascout-windows-x86_64.exe
   ```
4. Open `http://localhost:8787`.

### Data directory

Default: `%LOCALAPPDATA%\AthenaScout\`
(typically `C:\Users\<you>\AppData\Local\AthenaScout\`).

Override with `DATA_DIR`:

```powershell
$env:DATA_DIR = "D:\AthenaScout\data"
.\athenascout-windows-x86_64.exe
```

### Running on startup

Easiest: drop a shortcut to the `.exe` into your Startup folder.
Press `Win+R` and type `shell:startup` to open it, then drag a
shortcut in.

For something closer to a real service, [NSSM](https://nssm.cc/) wraps
any executable as a Windows service in a few lines.

### Windows SmartScreen

The PyInstaller binary is unsigned, so SmartScreen may warn on first
launch. Click **More info** → **Run anyway**. This is a one-time
prompt per binary version.

---

## macOS

1. Download `athenascout-macos-universal.dmg` from the
   [latest release](https://github.com/mnbaker117/AthenaScout/releases).
2. Open the `.dmg` and drag AthenaScout to `/Applications`.
3. Right-click AthenaScout in Finder and choose **Open**. The first
   launch requires this because the app is unsigned.
4. Confirm the security prompt.
5. Open `http://localhost:8787`.

### Data directory

Default:

```
~/Library/Application Support/AthenaScout/
```

Override with `DATA_DIR` if launching from a terminal:

```bash
DATA_DIR=~/AthenaScout /Applications/AthenaScout.app/Contents/MacOS/athenascout
```

### Gatekeeper

The macOS binary is unsigned. If macOS refuses to launch it even
after a right-click → Open dance, clear the quarantine attribute and
try again:

```bash
xattr -dr com.apple.quarantine /Applications/AthenaScout.app
```

---

## Pointing at your Calibre library

In standalone mode, the [first-run wizard](first-run.md) lets you
browse to your Calibre library on first launch. You can also set the
same environment variables that the Docker setup uses, with the same
semantics — except in standalone mode the paths are real host paths
(no container indirection):

```bash
CALIBRE_PATH=/home/me/calibre ./athenascout-linux-x86_64
CALIBRE_EXTRA_PATHS=/srv/audiobooks/calibre
```

Common Calibre library locations to point at:

- **Linux:** `~/Calibre Library/`
- **Windows:** `C:\Users\<you>\Calibre Library\`
- **macOS:** `~/Calibre Library/`

If you use multiple Calibre libraries, you can register them all from
the wizard or later from **Settings → Library Sources**.

---

## Updating

Download the new binary from the releases page and replace the old
one. Your data directory is untouched, so settings, libraries, and
your admin account survive.

Schema migrations apply automatically on first start of the new
version.

---

## Uninstalling

1. Stop AthenaScout (close the window or stop the service).
2. Delete the binary or the `.app`.
3. Optionally delete the data directory if you don't want to preserve
   your settings:
   - Linux: `rm -rf ~/.local/share/athenascout/`
   - Windows: delete `%LOCALAPPDATA%\AthenaScout\`
   - macOS: `rm -rf ~/Library/Application\ Support/AthenaScout/`

---

## Troubleshooting

**The binary won't start and prints nothing.**
Run it from a terminal (not by double-clicking) so you can see the
error output. The most common cause on Linux is a missing system
shared library — install your distro's `libstdc++` and `libffi`
packages.

**Port 8787 is already in use.**
Set `WEBUI_PORT` to something else before launching:
```bash
WEBUI_PORT=9090 ./athenascout-linux-x86_64
```

**I want to access AthenaScout from another machine on my LAN.**
The standalone build binds to all interfaces by default, so you
should be able to reach it at `http://<your-machine-ip>:8787`. If
not, check your host firewall.

**Before exposing AthenaScout beyond `localhost`**, read the
[auth deployment patterns doc](auth-deployment.md). Single-admin
auth is enabled by default but you should still understand the
threat model and pick a deployment pattern that fits your setup.
