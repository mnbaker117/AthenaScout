# Security Policy

## Threat Model

AthenaScout is designed for **single-administrator self-hosted deployment**.
The admin is trusted by definition — anyone with access to the AthenaScout
admin account is assumed to also have legitimate access to the underlying
Calibre library and the host system.

The application authenticates incoming requests against a single admin
account (configured during first-run setup). All API routes except a small
public allowlist (`/api/health`, `/api/platform`, `/api/auth/*`) require a
valid session cookie. Frontend static files are public so the login page
can render before the user is authenticated.

### What auth protects against

- Untrusted users on the same network discovering the app and accessing
  library data, scan history, or configuration
- Accidental exposure to a wider network than intended (e.g., a
  misconfigured firewall, an open VPN tunnel, an unintentionally
  port-forwarded router) leading to public discovery via search engines
  or scanners
- Compromised devices on the same LAN being used to scrape data or
  trigger destructive operations (mass scans, library resets, etc.)

### What auth does NOT protect against

- A malicious admin (the admin is always trusted)
- An attacker with shell access to the host (they can read the SQLite
  databases directly and bypass authentication entirely)
- Network-layer eavesdropping if AthenaScout is exposed over plain HTTP
  on an untrusted network — use HTTPS via a reverse proxy or rely on
  VPN-layer encryption (e.g., Tailscale)
- Brute force password attacks beyond basic rate limiting (5 failed
  attempts triggers a 5-minute lockout — adequate for a personal app
  on a private network, not sufficient for a public-facing deployment)
- Sophisticated client-side attacks (XSS via crafted book metadata,
  for example). The session cookie is `HttpOnly` and `SameSite=Lax`
  to mitigate the most common variants.

## Recommended Deployment Patterns

### Tailscale (recommended for personal use)

Install Tailscale on the AthenaScout host and on every device that needs
access. AthenaScout stays bound to the local network; remote access goes
through Tailscale's encrypted overlay network. No port forwarding or
public DNS records required.

Tailscale's free personal tier supports up to 100 devices, which is more
than enough for any single-user deployment.

This is the simplest secure remote-access option. Combined with
AthenaScout's built-in auth, it provides defense in depth: an attacker
would need to compromise both your Tailnet and your AthenaScout password.

### Reverse proxy with HTTPS

For deployments where Tailscale isn't suitable (e.g., sharing access with
people who can't install client software), put a reverse proxy in front
of AthenaScout:

- **Caddy** (easiest): automatic Let's Encrypt certificates via the
  Caddyfile syntax — usually a 4-line config
- **nginx** or **Traefik**: more configuration but well-documented

The reverse proxy should:

- Terminate HTTPS using a valid certificate
- Forward `X-Forwarded-Proto: https` so AthenaScout marks session
  cookies as `Secure` (the auth router checks this header to decide)
- Optionally add HTTP basic auth as an additional defense layer

### Trusted private LAN

If AthenaScout is only accessible from trusted devices on a private LAN
(no remote access at all), HTTPS is optional but the auth feature is
still recommended in case of guest devices or compromised IoT devices on
the same network.

### What NOT to do

- **Do NOT expose AthenaScout directly to the public internet over plain
  HTTP.** Session cookies and credentials would travel in cleartext and
  the rate limit is not strong enough to repel a determined attacker.
- **Do NOT disable auth.** It's the only application-layer access control
  AthenaScout has.
- **Do NOT share the admin credentials with untrusted users.** AthenaScout
  has no concept of read-only or limited users — anyone with the password
  has full control over scans, library configuration, and the database
  editor.

## Auth Architecture (How It Works)

- **Credentials** are stored in a dedicated `athenascout_auth.db` file
  inside the data directory (`/app/data` in Docker, the platform's app
  data dir on standalone). The file is locked down to mode `0600` on
  POSIX systems so only the owning user can read it.
- **Passwords** are hashed with bcrypt at work factor 12 via passlib.
  The salt is embedded in the hash string by bcrypt itself.
- **Sessions** are signed cookies (not JWTs) created with `itsdangerous`'s
  `URLSafeTimedSerializer`. The cookie payload is just the user ID + an
  issued-at timestamp; the signature is verified on every request by
  the auth middleware.
- **Cookie flags:** `HttpOnly`, `SameSite=Lax`, `Secure` (only when the
  request was over HTTPS), `Max-Age=30 days`, `Path=/`.
- **The signing secret** lives in `<data_dir>/auth_secret`, generated
  with `secrets.token_urlsafe(48)` on first run. Locked down to `0600`
  on POSIX. If the file is lost, the only side effect is that all current
  sessions are invalidated and users have to log in again.
- **Rate limiting:** 5 failed login attempts within any window triggers
  a 5-minute lockout. A successful login resets the counter.

## Known Limitations

These are documented design choices, not bugs. They are listed here for
transparency and may be addressed in future versions.

- **Single admin account.** No multi-user support, no read-only users,
  no role-based access control. If multiple people need access, they
  share the same account.
- **No password complexity enforcement.** The admin chooses their own
  password during setup; AthenaScout only checks length (8-256 chars).
- **No password recovery flow.** If the admin forgets their password,
  they must reset it by editing `athenascout_auth.db` directly via
  SQLite. This is intentional — automated password recovery flows are
  a common vulnerability surface and unnecessary for a single-admin
  self-hosted app.
- **No 2FA / TOTP.** Out of scope for v2.0; may be added in a future
  release.
- **No audit log of admin actions.** Login events (success and failure)
  are written to the application logs, but there's no separate audit
  trail of "what scans the admin triggered" or "what books they edited".
- **The `/api/libraries/validate-path` endpoint is intentionally a
  filesystem browser.** It allows the authenticated admin to navigate
  their own filesystem to find Calibre libraries during library setup.
  CodeQL flags this as "uncontrolled data used in path expression".
  The endpoint is gated by auth, performs only read-only filesystem
  operations, and includes input sanitization that rejects null bytes
  and excessively long paths. The flagged sites carry inline comments
  documenting the rationale; if any CodeQL alerts remain after rescan,
  they will be manually dismissed in the GitHub UI with a link back to
  this section.

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| v2.0.x  | ✓                  |
| v1.x    | ✗ (please upgrade) |
| < 1.0   | ✗                  |

Only the latest minor release of v2.0 receives security fixes. Older
patch releases should upgrade.

## Reporting a Vulnerability

If you discover a security vulnerability in AthenaScout, please report it
**privately** rather than opening a public GitHub issue.

**Contact:** open a GitHub Security Advisory via the repository's
"Security" tab → "Report a vulnerability". This creates a private
discussion that only the maintainer can see.

When reporting, please include:

- A description of the vulnerability
- Steps to reproduce or a proof of concept
- The affected version(s) of AthenaScout
- Your assessment of the severity (Low / Medium / High / Critical)
- Whether you'd like credit in the public disclosure (and how to credit
  you)

### Response timeline

This is a hobby project maintained by a single developer. Realistic
expectations:

- **Initial response:** within 7 days
- **Triage and severity assessment:** within 14 days
- **Patch release:** depends on severity — critical issues patched
  within 30 days, lower severity issues bundled into the next regular
  release

### Disclosure policy

Once a fix is released, the vulnerability and its mitigation will be
disclosed publicly via:

- A GitHub Security Advisory on the repository
- A line in the release notes
- Credit to the reporter (if they consent)

Coordinated disclosure timelines (e.g., "please don't disclose for 90
days") will be respected to the extent reasonable for a hobby project.
