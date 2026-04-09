# AthenaScout Documentation

User-facing documentation for AthenaScout.

If you're brand new, the [first-run walkthrough](first-run.md) is the
fastest way from "I just deployed it" to "I'm staring at a list of
books I'm missing."

## Getting started

- [**Docker setup**](setup-docker.md) — Docker Compose, Unraid,
  multi-library discovery, environment variables, the
  `CALIBRE_EXTRA_PATHS` rules
- [**Standalone setup**](setup-standalone.md) — native binaries for
  Linux, Windows, and macOS, with systemd / Startup folder / Gatekeeper
  notes
- [**First-run walkthrough**](first-run.md) — admin account, library
  discovery, your first author scan, where to go from there

## Optional integrations

- [**MyAnonamouse integration**](mam-integration.md) — token setup,
  format priority, multi-language scanning, single-book / single-author
  / full-library scans, the upload / download / missing-everywhere tabs

## Operations

- [**Authentication & deployment patterns**](auth-deployment.md) —
  threat model, single-admin auth, and three deployment patterns
  (Tailscale, reverse proxy, trusted LAN)
- [Security policy](../SECURITY.md) — vulnerability reporting and the
  full threat-model write-up

## For contributors

There's no formal contributor guide yet. The code is heavily commented
where it counts — start with the module docstrings in
[`app/main.py`](../app/main.py),
[`app/lookup.py`](../app/lookup.py),
[`app/sources/mam.py`](../app/sources/mam.py), and
[`app/sources/base.py`](../app/sources/base.py) (the `BaseSource`
contract that any new source plugin implements). PRs and issues are
welcome.
