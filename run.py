#!/usr/bin/env python3
"""
AthenaScout — Standalone Entry Point

Launches the FastAPI server for non-Docker (standalone) use.
Opens the user's default browser automatically.

Usage:
    python run.py                  # Start with browser auto-open
    python run.py --no-browser     # Start without opening browser
    python run.py --host 0.0.0.0   # Expose to LAN (default: localhost only)

For Docker deployments, use the Dockerfile CMD instead of this script.
"""
import os
import sys
import webbrowser
import threading
import argparse
from app.main import app as fastapi_app

# Ensure standalone mode is set before any app imports
os.environ.setdefault("ATHENASCOUT_MODE", "standalone")


def main():
    parser = argparse.ArgumentParser(description="AthenaScout Standalone Server")
    parser.add_argument(
        "--host",
        default=os.getenv("WEBUI_HOST", "127.0.0.1"),
        help="Host to bind to (default: 127.0.0.1, use 0.0.0.0 for LAN access)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("WEBUI_PORT", "8787")),
        help="Port to bind to (default: 8787)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't auto-open the browser on startup",
    )
    args = parser.parse_args()

    # Delayed browser open — gives the server time to start
    if not args.no_browser:
        def _open_browser():
            import time
            time.sleep(1.5)
            url = f"http://localhost:{args.port}"

            # PyInstaller bundles its own libssl/libcrypto and sets LD_LIBRARY_PATH
            # so they're found at runtime. Unfortunately that env var is inherited
            # by child processes — when webbrowser.open() spawns xdg-open/kde-open,
            # those system tools end up loading our bundled libs instead of the
            # system ones, causing version mismatch errors with libcurl. Restore
            # the original library paths just for this child process spawn.
            saved = {}
            for key in ('LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
                if key in os.environ:
                    saved[key] = os.environ[key]
                    orig = os.environ.get(f'{key}_ORIG')
                    if orig:
                        os.environ[key] = orig
                    else:
                        del os.environ[key]
            try:
                webbrowser.open(url)
            finally:
                # Restore in case anything else in the process needs the bundled libs
                for key, val in saved.items():
                    os.environ[key] = val

        threading.Thread(target=_open_browser, daemon=True).start()

    # Import uvicorn here so startup errors are clear
    try:
        import uvicorn
    except ImportError:
        print("Error: uvicorn not installed. Run: pip install uvicorn[standard]")
        sys.exit(1)

    # Startup banner
    local_url = f"http://localhost:{args.port}"
    network_url = f"http://{args.host}:{args.port}" if args.host != "127.0.0.1" else None

    print()
    print("  ╔═══════════════════════════════════════╗")
    print("  ║         AthenaScout (Standalone)       ║")
    print("  ╠═══════════════════════════════════════╣")
    print(f"  ║  Local:   {local_url:<28}║")
    if network_url:
        print(f"  ║  Network: {network_url:<28}║")
    print("  ║  Press Ctrl+C to stop                 ║")
    print("  ╚═══════════════════════════════════════╝")
    print()

    uvicorn.run(
        fastapi_app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=True,
    )


if __name__ == "__main__":
    main()
