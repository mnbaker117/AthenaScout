#!/usr/bin/env python3
"""
build_standalone.py — Build AthenaScout standalone executable.

Wraps PyInstaller with sensible defaults and pre-flight checks.

Usage:
    python build_standalone.py                # Full build
    python build_standalone.py --clean        # Wipe build cache first
    python build_standalone.py --skip-frontend  # Skip npm build (faster iteration)

Requirements:
    pip install -r requirements-build.txt
    Node.js + npm (unless --skip-frontend)
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()


def run(cmd, cwd=None, label=""):
    """Run a subprocess command with friendly output."""
    if label:
        print(f"\n→ {label}")
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, check=False)
    if result.returncode != 0:
        print(f"\n✗ Command failed with exit code {result.returncode}")
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(
        description="Build AthenaScout standalone executable",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--clean',
        action='store_true',
        help='Remove build/ and dist/ directories before building',
    )
    parser.add_argument(
        '--skip-frontend',
        action='store_true',
        help='Skip npm install and frontend build',
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  AthenaScout Standalone Builder")
    print("=" * 60)

    # ─── Pre-flight: Check PyInstaller ────────────────────────
    try:
        import PyInstaller
        print(f"\n✓ PyInstaller {PyInstaller.__version__} detected")
    except ImportError:
        print("\n✗ PyInstaller not installed.")
        print("  Install build dependencies: pip install -r requirements-build.txt")
        sys.exit(1)

    # ─── Clean step ───────────────────────────────────────────
    if args.clean:
        print("\n→ Cleaning build artifacts...")
        for d in ['build', 'dist']:
            p = ROOT / d
            if p.exists():
                print(f"  Removing {p}")
                shutil.rmtree(p)

    # ─── Frontend build ───────────────────────────────────────
    if not args.skip_frontend:
        frontend_dir = ROOT / 'frontend'
        if not frontend_dir.exists():
            print(f"\n✗ Frontend directory not found: {frontend_dir}")
            sys.exit(1)

        if not (frontend_dir / 'node_modules').exists():
            run(['npm', 'install'], cwd=frontend_dir, label="Installing frontend dependencies")

        run(['npm', 'run', 'build'], cwd=frontend_dir, label="Building frontend")

    # ─── Verify frontend dist exists ─────────────────────────
    dist_path = ROOT / 'frontend' / 'dist'
    if not dist_path.exists():
        print(f"\n✗ Frontend dist not found: {dist_path}")
        print("  Run without --skip-frontend or build manually first.")
        sys.exit(1)
    print(f"\n✓ Frontend dist verified: {dist_path}")

    # ─── PyInstaller build ────────────────────────────────────
    spec_file = ROOT / 'athenascout.spec'
    if not spec_file.exists():
        print(f"\n✗ Spec file not found: {spec_file}")
        sys.exit(1)

    run(
        ['pyinstaller', str(spec_file), '--noconfirm'],
        cwd=ROOT,
        label="Running PyInstaller (this may take a few minutes)",
    )

    # ─── Verify output ────────────────────────────────────────
    output_dir = ROOT / 'dist' / 'athenascout'
    if not output_dir.exists():
        print(f"\n✗ Build output not found: {output_dir}")
        sys.exit(1)

    # Find the executable (different name on Windows)
    exe_name = 'athenascout.exe' if sys.platform == 'win32' else 'athenascout'
    exe_path = output_dir / exe_name
    if not exe_path.exists():
        print(f"\n✗ Executable not found: {exe_path}")
        sys.exit(1)

    # Calculate output size
    total_size = sum(f.stat().st_size for f in output_dir.rglob('*') if f.is_file())
    size_mb = total_size / (1024 * 1024)

    print()
    print("=" * 60)
    print("  ✓ Build complete!")
    print("=" * 60)
    print(f"  Output:     {output_dir}")
    print(f"  Executable: {exe_path}")
    print(f"  Total size: {size_mb:.1f} MB")
    print()
    print(f"  To run:")
    print(f"    cd {output_dir}")
    print(f"    ./{exe_name}")
    print()


if __name__ == '__main__':
    main()
