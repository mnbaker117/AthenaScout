"""
Runtime Environment Detection — detects Docker vs standalone mode,
OS type, and provides platform-aware default paths.

This module is intentionally free of app imports (no config.py, no database.py)
to avoid circular dependencies. It's imported early by config.py at module level.

Detection is computed once at import time — no async needed.
"""
import os
import sys
import platform as _platform
from pathlib import Path

# ─── Runtime Mode Detection ──────────────────────────────────

def _detect_runtime_mode() -> str:
    """Detect if running in Docker or standalone.

    Priority:
    1. ATHENASCOUT_MODE env var (explicit override)
    2. Auto-detect via /.dockerenv file
    3. Auto-detect via /proc/1/cgroup containing 'docker' or 'containerd'
    4. Default: standalone
    """
    override = os.getenv("ATHENASCOUT_MODE", "").lower().strip()
    if override in ("docker", "standalone"):
        return override

    # Check for Docker's marker file
    if Path("/.dockerenv").exists():
        return "docker"

    # Check cgroup for container indicators
    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.exists():
            text = cgroup.read_text()
            if "docker" in text or "containerd" in text:
                return "docker"
    except (PermissionError, OSError):
        pass

    return "standalone"


def _get_os_type() -> str:
    """Get normalized OS type string.

    Returns: 'linux', 'macos', or 'windows'
    """
    system = _platform.system().lower()
    if system == "darwin":
        return "macos"
    # Covers 'linux' and 'windows' directly
    return system


# Module-level constants — computed once at import
RUNTIME_MODE = _detect_runtime_mode()
OS_TYPE = _get_os_type()
IS_DOCKER = RUNTIME_MODE == "docker"
IS_STANDALONE = RUNTIME_MODE == "standalone"


# ─── Data Directory ──────────────────────────────────────────

def get_data_dir() -> Path:
    """Get the appropriate data directory for the current platform.

    Docker: /app/data (set by Dockerfile ENV, handled by config.py)
    Linux standalone: ~/.local/share/athenascout/ (XDG_DATA_HOME)
    macOS standalone: ~/Library/Application Support/AthenaScout/
    Windows standalone: %LOCALAPPDATA%/AthenaScout/
    """
    if IS_DOCKER:
        return Path("/app/data")

    if OS_TYPE == "windows":
        base = os.environ.get("LOCALAPPDATA", "")
        if base:
            return Path(base) / "AthenaScout"
        return Path.home() / "AppData" / "Local" / "AthenaScout"

    if OS_TYPE == "macos":
        return Path.home() / "Library" / "Application Support" / "AthenaScout"

    # Linux — respect XDG_DATA_HOME
    xdg = os.environ.get("XDG_DATA_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "athenascout"


# ─── Default Library Paths ───────────────────────────────────

def get_default_library_paths() -> list[dict]:
    """Get OS-appropriate default library path suggestions.

    Returns a list of dicts with 'path', 'app_type', and 'description'.
    These are suggestions for the setup wizard — the paths may or may not
    exist on the user's system.
    """
    home = str(Path.home())

    if OS_TYPE == "windows":
        appdata = os.environ.get("APPDATA", "")
        return [
            {
                "path": os.path.join(home, "Calibre Library"),
                "app_type": "calibre",
                "description": "Default Calibre library location",
            },
            {
                "path": os.path.join(appdata, "calibre") if appdata else "",
                "app_type": "calibre",
                "description": "Calibre configuration directory",
            },
        ]

    if OS_TYPE == "macos":
        return [
            {
                "path": os.path.join(home, "Calibre Library"),
                "app_type": "calibre",
                "description": "Default Calibre library location",
            },
        ]

    # Linux
    return [
        {
            "path": os.path.join(home, "Calibre Library"),
            "app_type": "calibre",
            "description": "Default Calibre library location",
        },
        {
            "path": os.path.join(home, "calibre"),
            "app_type": "calibre",
            "description": "Alternative Calibre library location",
        },
    ]


# ─── Platform Info ────────────────────────────────────────────

def get_platform_info() -> dict:
    """Aggregate all platform info into a single dict for the API.

    Used by GET /api/platform to inform the frontend about the
    runtime environment, setup wizard needs, and path suggestions.
    """
    return {
        "runtime_mode": RUNTIME_MODE,
        "os_type": OS_TYPE,
        "is_docker": IS_DOCKER,
        "is_standalone": IS_STANDALONE,
        "data_dir": str(get_data_dir()),
        "default_library_paths": get_default_library_paths(),
        "python_version": _platform.python_version(),
        "platform_detail": _platform.platform(),
    }
