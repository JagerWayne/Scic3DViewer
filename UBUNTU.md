# Scic3DViewer on Ubuntu (22.04 or newer), installed in /home/wayne/python/s3dv

The site runs as a systemd service under the user `wayne`, listens only on `127.0.0.1:6711`, and Caddy in front of it
provides the public HTTPS address. Models are not stored on the server (they live in each user's Google Drive); the server keeps
only the database and `secret.key` in `data/`.

## 0. What you need
* An Ubuntu server and a **domain name** whose DNS `A` record points to the server (Google sign-in does not work on a bare IP or plain http).
* The ports 80 and 443 reachable from the internet (`sudo ufw allow 80,443/tcp`; do **not** open 6711).
* Your Google file `credentials.json` (`GOOGLE_DRIVE_SETUP.md`, Part A) with the redirect URI `https://YOUR-DOMAIN/auth/google/callback`, and the Google app published.

## 1. Copy the project to the server
On your Windows machine, in PowerShell inside the project folder (replace `YOUR-SERVER`):
```powershell
tar --exclude=.venv --exclude=data --exclude=backups --exclude=__pycache__ --exclude=.pytest_cache --exclude=token.json -czf ..\s3dv.tgz .
scp ..\s3dv.tgz wayne@YOUR-SERVER:/tmp/
ssh wayne@YOUR-SERVER "mkdir -p /home/wayne/python/s3dv && tar -xzf /tmp/s3dv.tgz -C /home/wayne/python/s3dv && rm /tmp/s3dv.tgz"
```
This copies `credentials.json` too if it is in the folder, and never copies `.venv`, `data`, `backups` or `token.json`.
(Linux to Linux: `rsync -av --exclude .venv --exclude data --exclude backups ./ wayne@YOUR-SERVER:/home/wayne/python/s3dv/`.)

If a script complains about `\r` or "bad interpreter", it has Windows line endings: `sed -i 's/\r$//' *.sh scic3d.service`.

## 2. Install (on the server)
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
cd /home/wayne/python/s3dv
bash setup.sh              # venv + dependencies + creates .env
nano .env                  # set SCIC_BASE_URL=https://YOUR-DOMAIN  (and the optional SMTP / Stripe lines)
```

## 3. Try it, then run it as a service
```bash
./run.sh                   # Ctrl+C to stop; check it with: curl -I http://127.0.0.1:6711/studio
./install-service.sh       # installs + starts the systemd service (asks for sudo)
```
Useful commands: `systemctl status scic3d`, `journalctl -u scic3d -f` (live log), `sudo systemctl restart scic3d` (after editing `.env` or updating the code).

## 4. HTTPS with Caddy
```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
sudo cp /home/wayne/python/s3dv/Caddyfile.ubuntu /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile          # replace 3d.example.com with your domain
sudo systemctl reload caddy
```
(These are Caddy's official install steps; if they ever change, see caddyserver.com/docs/install. `sudo apt install caddy` from Ubuntu's own repository also works.)
Open `https://YOUR-DOMAIN/studio`, sign in with Google, then `/admin` with the admin account.

## 5. Daily life
| Task | Command |
|---|---|
| Create / list licenses | the **/admin** page, or `./admin.sh new`, `./admin.sh list` |
| Backup (database + `secret.key`) | `./backup.sh`  (files appear in `backups/`) |
| Automatic nightly backup | `crontab -e` and add: `0 3 * * * /home/wayne/python/s3dv/backup.sh >/dev/null 2>&1` |
| Update the code | `git pull`, run `bash setup.sh` (it installs new Python packages such as Pillow), then `sudo systemctl restart scic3d` |
| Look at errors | `journalctl -u scic3d -n 100 --no-pager` |

Copy the `backups/` zips off the server regularly. They contain secrets: keep them private. Without `secret.key` every user has to reconnect Google Drive.

## Troubleshooting
| Problem | Fix |
|---|---|
| `Permission denied` running `./run.sh` | `chmod +x *.sh` (or `bash run.sh`) |
| Service fails: `status=203/EXEC` | `.venv` is missing: run `bash setup.sh` as `wayne`, then restart the service |
| Service fails: `status=226/NAMESPACE` or cannot write | the folder must be exactly `/home/wayne/python/s3dv`, owned by `wayne`: `sudo chown -R wayne:wayne /home/wayne/python/s3dv` |
| Google: `redirect_uri_mismatch` | the redirect URI in Google Cloud must be exactly `https://YOUR-DOMAIN/auth/google/callback`, and `SCIC_BASE_URL` in `.env` must be the same domain; restart after editing |
| Caddy cannot get a certificate | the domain must point to this server and ports 80/443 must be open |
| Uploads over 1 MB fail | you are behind another proxy (nginx): set `client_max_body_size 520m;` there |
| Everything works but the page has no styling | the browser needs internet (Three.js and Tailwind load from CDNs) |

## Security notes
* `.env`, `credentials.json`, `data/` and `backups/` are private to `wayne` (`setup.sh` sets the permissions). Never put them in a public repository.
* The app trusts the `X-Forwarded-For` header only from `127.0.0.1` (the local Caddy), so rate limits see real visitor addresses.
* Keep Ubuntu updated: `sudo apt update && sudo apt upgrade`, and consider `sudo apt install unattended-upgrades`.
