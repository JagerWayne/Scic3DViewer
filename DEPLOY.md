# Putting Scic3DViewer on the internet

> Installing straight on an Ubuntu server (no Docker), in `/home/wayne/python/s3dv`? Follow **[UBUNTU.md](UBUNTU.md)** instead.

Links like `http://127.0.0.1:6711/v/...` only work on your own computer. To share with other people the site needs a
public address with HTTPS. The easiest way is Docker on any small server (a VPS from about 5 EUR/month works).

## 1. Prepare the server
1. A server with Docker + Docker Compose, and a domain name (for example `3d.example.com`) pointing to the server's IP.
2. Copy this project folder to the server. Do **not** copy `.venv`, `data` or `token.json`.
3. Put your Google file `credentials.json` into the project folder (see `GOOGLE_DRIVE_SETUP.md`, Part A).
   In Google Cloud add the redirect URI `https://3d.example.com/auth/google/callback` and **publish** the Google app.

## 2. Settings: create a file named `.env` next to `docker-compose.yml`
```
DOMAIN=3d.example.com
ADMIN_EMAIL=waynescicluna.1985@gmail.com

# optional: license emails to customers
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=you@example.com
SMTP_PASSWORD=app-password
SMTP_FROM=Scic3DViewer <you@example.com>

# optional: paid upgrades with Stripe (see below)
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
SCIC_PRICES={"PRO:1m":"price_123","PRO:1y":"price_456","ENTERPRISE:1y":"price_789"}
```

## 3. Start
```bash
docker compose up -d --build
```
Open `https://3d.example.com/studio`, sign in with Google, then open `/admin` (only the `ADMIN_EMAIL` account can).

## Stripe (optional)
1. Stripe Dashboard: create one **one-time price** per plan (for example PRO for 1 month, PRO for 1 year). Put the price ids in `SCIC_PRICES` as `TIER:duration` (`1m`, `3m`, `6m`, `1y`).
2. Developers, then Webhooks: add the endpoint `https://3d.example.com/api/billing/webhook`, event `checkout.session.completed`; copy the signing secret into `STRIPE_WEBHOOK_SECRET`.
3. An **Upgrade** button now appears in the Studio. A paid checkout creates the license for the customer's Google email automatically (and emails them if SMTP is set).
   Test first with Stripe test keys. Refunds are handled in Stripe; revoke the license in the admin panel.

## Backups (do this regularly)
```bash
docker compose exec app python backup.py     # or double-click backup.bat when running without Docker
```
The zip holds the database and `secret.key`. Without `secret.key` the stored Google logins cannot be read and every user must reconnect Drive.
Keep the zips somewhere safe and private. The models themselves live in each user's own Google Drive, not on the server.

## Updating
Copy the new files over the old ones (keep `data/`, `.env`, `credentials.json`) and run `docker compose up -d --build`.

## Without Docker
`setup.bat` then `run.bat` on Windows. Set `SCIC_BASE_URL` and the other settings as environment variables, and put any HTTPS reverse proxy (Caddy, nginx) in front.
