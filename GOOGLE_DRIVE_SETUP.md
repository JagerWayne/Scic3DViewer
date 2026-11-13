# Google sign-in and Google Drive in Scic3DViewer: how it works and how to set it up

**Sign-in.** The only way to log in is **Sign in with Google**. Google confirms the user's email; a license you create with
`python admin.py new` applies automatically to the Google account with that email. The same Google app set up in Part A
powers both the sign-in and the Drive connection.

**Drive.** Every user connects **their own** Google Drive from inside the Studio (one button).
When that user clicks *Share link*, the model is saved in **their** Drive, in a folder called `Scic3DViewer`.
The server keeps no copy. When a visitor opens the link, the server fetches the model from the owner's Drive and shows it;
visitors never get a Drive link or access to anyone's Drive.

There are two separate jobs:

| Who | What | How often | Needs Google Cloud? |
|---|---|---|---|
| **Part A: Site owner** (the person who runs this server) | Registers the site with Google once, so a "Connect Google Drive" button can exist | **Once** | Yes |
| **Part B: Every user** | Clicks "Connect Drive" and allows access | Once per user | **No** |

Users never see Google Cloud, never handle files, and never run scripts. If you are a user and the Drive dialog says
*"Google Drive is not enabled on this site yet"*, the owner has not done Part A: send them this page.

> Google renames screens now and then. The words in **bold** are what to look for; use the search box at the top of
> the Google Cloud page if you get lost.

---

# Part A. Site owner: register the site with Google (once, about 15 minutes)

**You need:** a Google account (Gmail is fine) and this project installed (double-click `setup.bat`).
You are *not* publishing an app to the public store. You are only telling Google "this website is allowed to ask
people for permission". It is free.

## A1. Create a Google Cloud project
1. Open <https://console.cloud.google.com/> and sign in. Accept the Terms of Service if asked.
2. At the top, click the **project picker** (next to the Google Cloud logo) → **New project**.
3. **Project name:** `Scic3DViewer` → **Create**. Wait for the bell icon notification, then select the project so `Scic3DViewer` shows at the top.

## A2. Turn on the Google Drive API
1. Menu (three lines) → **APIs & Services** → **Library**.
2. Search `Google Drive API` → open **Google Drive API** → **Enable**.
> Check: the page now shows **Manage** instead of *Enable*.

## A3. Set up the permission screen
1. Menu → **APIs & Services** → **OAuth consent screen** (newer name: **Google Auth Platform**; click **Get started** if shown).
2. **App name:** the name your users will see, e.g. `Scic3DViewer`. **User support email:** your email. → **Next**.
3. **Audience:** **External** → **Next**. **Contact email:** your email → **Next**. Tick the policy box → **Continue** → **Create**.
   Then open **Branding** and fill in **Application home page** `https://YOUR-DOMAIN/` (the public landing page, NOT /studio), **Privacy policy** `https://YOUR-DOMAIN/privacy` and **Terms of service** `https://YOUR-DOMAIN/terms` (these pages are built into the site; set `SCIC_OPERATOR` and `SCIC_CONTACT_EMAIL` in `.env`), and add your domain under **Authorized domains**.
4. Open **Data Access** → **Add or remove scopes** → make sure these are ticked: **`openid`**, **`.../auth/userinfo.email`**, **`.../auth/userinfo.profile`** (sign-in; usually already there) and **`.../auth/drive.file`** (Drive) → **Update** → **Save**.
   This is the narrow permission: the site can only touch files **it creates itself**, never the user's other files.

## A4. Create the "Web application" client
1. Menu → **APIs & Services** → **Credentials** (newer: **Google Auth Platform → Clients**) → **+ Create credentials → OAuth client ID** (newer: **+ Create client**).
2. **Application type: Web application** (not "Desktop app").
3. **Name:** `Scic3DViewer web`.
4. Under **Authorized redirect URIs** click **+ Add URI** and type the address of your site followed by `/auth/google/callback`:
   * Testing on your own computer: `http://127.0.0.1:6711/auth/google/callback`
   * Real website: `https://your-domain.com/auth/google/callback` (Google requires **https** for real websites)
   > It must match **exactly**, including `http`/`https`, the port and no trailing slash. You may add several (local + real).
5. **Create** → **Download JSON**.

## A5. Put the file in the project
1. Move the downloaded file (a long name like `client_secret_123....json`) into the project folder (next to `run.bat`).
2. Rename it exactly to `credentials.json`.
3. **Real website only:** tell the server its public address before starting it (Windows terminal):
   ```
   set SCIC_BASE_URL=https://your-domain.com
   ```
   (On your own computer you can skip this: the default is `http://127.0.0.1:6711`.)
4. Start (or restart) the server with `run.bat`.

> `credentials.json` contains the site's secret. Never email it, post it or put it in a public repository.

## A6. Let other people connect (do not skip)
By default the Google app is in **Testing** mode: **only people you list can sign in or connect Drive at all**, and Drive logins expire after 7 days.
For a site used by other people:
1. **Google Auth Platform → Audience** (older: *OAuth consent screen*) → click **Publish app** → confirm.
2. Because the site only asks for the narrow `drive.file` permission, Google normally does **not** require a review.
   If Google asks for a logo, privacy-policy link or domain check, fill in what it asks for.
3. While you only test yourself you may stay in Testing mode: add your own Gmail under **Audience → Test users → + Add users**.

> Until you publish, other users get *"Error 403: access_denied"* when they click **Sign in with Google**.

## A7. Test it
1. Open the Studio, register, click **Connect Drive** (top bar) and follow Part B yourself.
2. Load a model → **Share link** → **Publish** → open the link in another tab.
3. In <https://drive.google.com/> you now have a folder **Scic3DViewer** with a file like `AbC12xYz.glb`.

---

# Part B. Every user: connect your own Google Drive (1 minute, no technical knowledge)

0. Open the Studio and click **Sign in with Google**; choose your account. (If you bought a Pro/Enterprise license, use the Google account with the email it was issued to.)
1. Click **Connect Drive** in the top bar (or just click **Share link**: it opens the same window).
2. Click **Connect Google Drive**. Your browser goes to Google.
3. **Choose your Google account.**
4. If you see **"Google hasn't verified this app"**: click **Advanced**, then **Go to … (unsafe)**. This is normal for
   small independent sites; the site only gets access to files it creates itself.
5. The permission says *"See, edit, create and delete only the specific Google Drive files you use with this app"*.
   Click **Continue** (or **Allow**).
6. You return to the Studio with the message **"Google Drive connected"** and the top-bar button reads **Drive ✓**.

From now on **Share link → Publish** saves the model in your Drive folder `Scic3DViewer` and gives you a link to send.

**Good to know**
* Only **you** (the owner of the link) store the file, in **your** Drive, using your free space (15 GB free on personal accounts).
* Do **not** delete or rename files inside `Scic3DViewer` by hand: the matching share link would stop working.
  To remove a link, delete it in the app: it also deletes the Drive file.
* **Disconnecting** (Connect Drive window → *Disconnect*) makes **all your existing links stop loading** until you connect again.
  You can also remove access at <https://myaccount.google.com/permissions> (same effect).
* Each license/account connects separately: your colleagues' Drive connections are not shared with yours.

---

# Troubleshooting

| What you see | Who fixes it | What to do |
|---|---|---|
| Sign-in page says *"Google sign-in is not set up on this site yet"* | Owner | Do Part A (and restart the server). Nobody can log in until `credentials.json` exists |
| I signed in but my Pro/Enterprise license does not unlock | Owner / user | Sign in with the Google account whose email matches the license (`python admin.py list` shows it). Another Google account gets Free |
| Drive window says *"Google Drive is not enabled on this site yet"* | Owner | Do Part A (and restart the server). `credentials.json` must be in the project folder |
| Google page: **Error 403: access_denied** | Owner | App is still in *Testing*: do A6 (Publish app) or add that person under *Test users* |
| Google page: **Error 400: redirect_uri_mismatch** | Owner | The URI in A4 does not match exactly. Open the client in Google Cloud and fix **Authorized redirect URIs** to `<site address>/auth/google/callback`. After a change wait a few minutes. On a real site also check `SCIC_BASE_URL` |
| Google page: *"Access blocked: This app's request is invalid"* | Owner | Client was created as *Desktop app*: create a **Web application** client (A4) |
| Back in the Studio: **"Could not connect Google Drive, please try again"** | User | Click Connect again within a few minutes (the sign-in link expires after 10 minutes) and press **Continue** on Google's page |
| *"Drive API has not been used in project..."* | Owner | A2 (wait 1 to 2 minutes after enabling) |
| Sharing says *"Connect your Google Drive first"* | User | Connect Drive (Part B); also after you revoked access at Google |
| Sharing says *"Upload to your Google Drive failed"* | User | Check internet and free Drive space; if it persists click Connect Drive → Disconnect → connect again |
| Visitors see *"This model is temporarily unavailable"* | User (link owner) | Their Drive was disconnected, full of errors or the file was deleted: reconnect Drive; if the file is gone, publish again |
| After about 7 days everything stops with *invalid_grant* | Owner | The Google app is still in *Testing* mode: publish it (A6). Users reconnect once |

---

# Security notes for the owner

* Each user's Google login token is stored **encrypted** (AES via Fernet) in the database. The key is `data/secret.key`:
  **back it up together with the database and never share it**. If you lose it, all users must reconnect.
* The site can only access files it created itself (`drive.file`), in a folder per user.
* Run the site on **https** for real use (Google requires it for the redirect, and it protects sessions and licenses).
* `credentials.json` = the site's identity at Google. Keep it private. If it leaks: Google Cloud → Credentials → open the client → **Reset secret** (download the new JSON; users stay connected).
* Environment variables (optional): `SCIC_BASE_URL` (public address), `SCIC_GOOGLE_CLIENT` (other path for the JSON),
  `SCIC_DRIVE_FOLDER` (folder name, default `Scic3DViewer`), `SCIC_DATA` (database folder).
