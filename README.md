# Scic3DViewer

FastAPI + SQLite backend, Three.js + Tailwind frontend. The SQLite database and activity log (table `activity`) live
under `./data` (override with `SCIC_DATA`). Shared models are stored only in Google Drive (see below).

## Run

```bash
pip install -r requirements.txt
python admin.py list            # creates ./data on first run
uvicorn app:app --host 127.0.0.1 --port 6711
```

Open `http://127.0.0.1:6711/studio`. Put it behind an HTTPS reverse proxy (Caddy, nginx) before exposing it to the internet.
The frontend loads Three.js and Tailwind from CDNs, so the browser needs internet access.

## Google Drive storage (every user's own Drive)

Each user clicks **Connect Drive** in the Studio and allows access to **their own** Google Drive. Shared models are saved
there (folder `Scic3DViewer`) and streamed back through the server, so visitors never get a Drive link. Nothing is shared
from local disk (`data/shares` is only a temporary upload staging folder). Users' Google tokens are stored encrypted
(`data/secret.key`, back it up). Without a connection a user cannot publish links.

The **site owner** must register the site with Google once (OAuth client of type *Web application* saved as
`credentials.json`, redirect URI `<your address>/auth/google/callback`, set `SCIC_BASE_URL` on a real site, and publish
the Google app so other people may connect). Full step-by-step guide for owners and users:
**[GOOGLE_DRIVE_SETUP.md](GOOGLE_DRIVE_SETUP.md)**.

## Admin panel

Sign in to `/studio` with the admin Google account (`waynescicluna.1985@gmail.com`, change it with the `SCIC_ADMIN_EMAIL`
setting), then click **Admin** in the top bar or open `/admin`. The admin gets the Enterprise tier automatically. Tabs:

* **Overview**: people, paid/pending/expiring licenses, links, views, connected drives, a 14-day activity chart, system status (Google, email, Stripe).
* **Licenses**: search and filter. **Give a license** to anyone who has signed in: click **Give license** on a Free row (or **Edit**), choose Free, Pro or Enterprise and the length, **Save**; it applies to them right away. **+ New license** prepares one for an email that has not signed in yet (the clock starts at their first sign-in, or tick *Start the license clock now*). **Edit** changes Name & Surname, email, tier, **status** (Active, Paused or Revoked, any can be switched to any other), expiry date or duration, an admin note, and has quick buttons (+30 days, +1 year, Pause/Resume, Revoke/Restore, resend email) and a Danger zone. **Pause** gives the Free tier and freezes the clock (the paused time is added back on Resume); **Revoke** blocks for good. **Delete** removes the license only (the person keeps account and links); **Delete person and all data** also removes links (and the files in their Drive), Drive login, sessions and log, after typing the email. The admin account cannot be deleted.
* **Shared links**: every link with owner, views and status; revoke, restore or delete (also deletes the file from the owner Drive).
* **Activity**: searchable log of sign-ins, shares, views and admin actions.

Only a session whose Google-verified email equals the admin email is accepted by the `/api/admin/*` routes, so the page
itself being reachable by anyone does not matter.

## Features by area

* **Studio**: parts outliner, gizmo, merge, paint and edge-aware fill, material and HDRI, lighting presets with soft shadow, slicer, exploded view, ruler/angle/thickness, callouts with spotlight and tour, pedestal, simplify, transparent PNG and 360 WebM.
* **Formats**: open GLB, GLTF, OBJ, STL, FBX, 3MF and (Enterprise) STEP; export GLB, OBJ, STL and USDZ (iPhone AR; vertex colors are not part of USDZ).
* **Sharing**: My projects panel (copy, open, edit and republish to the same address, revoke, delete), link expiry and password (Pro and up), QR code, embed code, fullscreen and auto-rotate in the viewer. Models are stored gzip-compressed (lossless) in the owner Drive.
* **Business**: license emails (SMTP), Stripe checkout that creates licenses automatically, Ubuntu install with systemd + Caddy HTTPS: **[UBUNTU.md](UBUNTU.md)**; Docker kit and backups: **[DEPLOY.md](DEPLOY.md)**.

## Licenses (admin only, local CLI, the admin panel does the same)

```bash
python admin.py new                       # prompts: Name & Surname, email, tier, duration (30d / 6m / 1y)
python admin.py list
python admin.py upgrade user@mail.com PRO
python admin.py revoke  user@mail.com
```

* Users sign in with **Google** (the only login). A license is attached to the **email** you enter in `admin.py new`:
  when that person signs in with that Google account, the tier unlocks automatically (no key to type).
* The duration starts at their first sign-in (month = 30 days, year = 365 days).
* Everyone else gets the Free tier. Expired or revoked licenses fall back to Free automatically.
* Google sign-in uses the same Google app as Drive (see `GOOGLE_DRIVE_SETUP.md`); the site cannot be used until that one-time setup is done.

## Tiers (enforced by the server)

| | Free | Pro | Enterprise |
|---|---|---|---|
| Upload / share size | 15 MB | 100 MB | 500 MB |
| Callouts | 2 | unlimited | unlimited |
| New links per month | 30 | 150 | 5000 |
| Share permissions | view-only | download (GLB), explode | + ruler/caliper, GLB/OBJ/STL |

### 360° picture links (view-only)

A link can be published as pictures instead of a 3D model: the Studio photographs the model from many angles into one
pack file in the owner's Drive, and the visitor's browser only receives pictures (no Three.js, no model; dragging shows the
nearest picture). The original GLB stays private in the owner's Drive for editing and is only served if the owner allowed downloads.
The server enforces the limits and the watermark; the browser cannot choose them.

| | Free | Pro | Enterprise |
|---|---|---|---|
| Quality presets | Light (36×3, 800 px) | + Standard (72×5, 1024 px) | + High (120×7, 1400 px) and Custom (up to 180×12, 2048 px) |
| Watermark | forced text + faint cross | none | optional own text |
| Model download on the link | no | GLB | GLB |

Measure, exploded view and part visibility are not available on picture links. Pictures can still be screenshotted.

The server checks size, callout count, share permissions, and custom `.hdr` uploads. Editing tools (slicer, paint fill,
decimation, ...) run in the browser and are gated in the UI only: a determined user can bypass them with devtools.
They cannot get a bigger upload, extra share permissions, or more formats on shared links.

## Security notes

* Bearer-token sessions (30 days, stored hashed), no cookies, so no CSRF. Tokens live in `localStorage`; a strict CSP (per-request nonce) limits XSS.
* Rate limits on register / share / view, request size cap, GLB magic-byte check, `.hdr` check, random 72-bit share ids.
* Shared links are public to anyone with the URL, and the model is served as a normal file (no obfuscation, by design).
* Emails are verified by Google (no typing someone else's email). The rate limiter is in-memory and per-process.

## Known limits

* Merge All and Decimate drop textures/UVs (colors are baked into vertex colors). Hidden parts are not published.
* The 360° WebM is recorded from the transparent canvas, so background transparency depends on the browser's codec.
* Tests: `.venv\Scripts\python -m pip install pytest httpx playwright` and `.venv\Scripts\python -m playwright install chromium` once, then `.venv\Scripts\python -m pytest tests` (API tests plus browser tests; the browser tests need internet and are skipped without Playwright).
