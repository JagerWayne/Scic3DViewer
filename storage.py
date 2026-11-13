"""Per-user Google Drive storage. Each license connects its OWN Google account through the web OAuth flow;
the refresh token is stored encrypted in SQLite (key: data/secret.key). The server owner only provides the OAuth client."""
import io, json, os, secrets, time
from pathlib import Path

from cryptography.fernet import Fernet

import db

ROOT = Path(__file__).parent
CLIENT = Path(os.environ.get("SCIC_GOOGLE_CLIENT", ROOT / "credentials.json"))  # OAuth client of type "Web application"
BASE_URL = os.environ.get("SCIC_BASE_URL", "http://127.0.0.1:6711").rstrip("/")
REDIRECT = BASE_URL + "/auth/google/callback"
FOLDER_NAME = os.environ.get("SCIC_DRIVE_FOLDER", "Scic3DViewer")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]  # only files this app created
LOGIN_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"]
if BASE_URL.startswith(("http://127.0.0.1", "http://localhost")):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # plain http is only tolerated for local testing
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
_pending: dict = {}   # state -> (license_id, code_verifier, expires)   shortcut: in-memory, single process
_svc: dict = {}       # license_id -> (service, folder_id)


def configured() -> bool:
    return CLIENT.exists()


def _fernet() -> Fernet:
    key = db.DATA / "secret.key"
    if not key.exists():
        key.write_bytes(Fernet.generate_key())
    return Fernet(key.read_bytes())


def connected(lid: int) -> bool:
    with db.connect() as c:
        return c.execute("SELECT 1 FROM drive_tokens WHERE license_id=?", (lid,)).fetchone() is not None


def _flow(scopes, state=None):
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_secrets_file(str(CLIENT), scopes, redirect_uri=REDIRECT, state=state)


def begin(purpose: str, lid: int | None = None) -> str:
    """Google consent URL. purpose='login' (identity + Drive in one screen) or 'drive' (reconnect, needs the license id)."""
    flow = _flow(LOGIN_SCOPES + SCOPES if purpose == "login" else SCOPES); state = secrets.token_urlsafe(24)
    # offline access gives the refresh token Drive needs; the first consent always returns it, "consent" forces it when reconnecting
    extra = {"prompt": "select_account", "access_type": "offline"} if purpose == "login" else {"access_type": "offline", "prompt": "consent"}
    url, _ = flow.authorization_url(state=state, **extra)
    now = time.time()
    for k in [k for k, v in _pending.items() if v[3] < now]:
        _pending.pop(k)
    if len(_pending) > 1000:
        raise ValueError("busy")
    _pending[state] = (purpose, lid, flow.code_verifier, now + 600)
    return url


def purpose(state: str):
    return _pending.get(state, (None,))[0]


def finish(state: str, code: str):
    """One-time state check + code exchange. Returns ('login', None, google_claims) or ('drive', license_id, None)."""
    kind, lid, verifier, exp = _pending.pop(state, (None, None, None, 0))
    if kind is None or exp < time.time():
        raise ValueError("Sign-in expired, please try again")
    flow = _flow(LOGIN_SCOPES + SCOPES if kind == "login" else SCOPES, state); flow.code_verifier = verifier; flow.fetch_token(code=code)
    creds = flow.credentials
    if kind == "login":
        from google.auth.transport import requests as greq
        from google.oauth2 import id_token
        claims = id_token.verify_oauth2_token(creds.id_token, greq.Request(), flow.client_config["client_id"])
        if not claims.get("email_verified") or not claims.get("email"):
            raise ValueError("Google did not confirm this email")
        # Google lets people untick the Drive box: then they are still signed in, just without a Drive connection
        if creds.refresh_token and SCOPES[0] in set(getattr(creds, "granted_scopes", None) or creds.scopes or []):
            claims["drive_token"] = creds.to_json()
        return kind, None, claims
    if not creds.refresh_token:
        raise ValueError("Google did not grant offline access")
    save_token(lid, creds.to_json())
    return kind, lid, None


def save_token(lid: int, creds_json: str):
    """Store (replace) the user's Google Drive login, encrypted."""
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO drive_tokens(license_id,token_enc,connected_at) VALUES(?,?,?)",
                  (lid, _fernet().encrypt(creds_json.encode()).decode(), int(time.time())))
    _svc.pop(lid, None)


def disconnect(lid: int):
    with db.connect() as c:
        row = c.execute("SELECT token_enc FROM drive_tokens WHERE license_id=?", (lid,)).fetchone()
        c.execute("DELETE FROM drive_tokens WHERE license_id=?", (lid,))
    _svc.pop(lid, None)
    if row:  # best effort: also revoke at Google
        try:
            import requests
            t = json.loads(_fernet().decrypt(row[0].encode()))
            requests.post("https://oauth2.googleapis.com/revoke", params={"token": t.get("refresh_token")}, timeout=10)
        except Exception:
            pass


def _service(lid: int):
    if lid not in _svc:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        with db.connect() as c:
            row = c.execute("SELECT token_enc FROM drive_tokens WHERE license_id=?", (lid,)).fetchone()
        if not row:
            raise PermissionError("Google Drive is not connected")
        creds = Credentials.from_authorized_user_info(json.loads(_fernet().decrypt(row[0].encode())), SCOPES)
        s = build("drive", "v3", credentials=creds, cache_discovery=False)
        q = f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        found = s.files().list(q=q, fields="files(id)", pageSize=1).execute()["files"]
        folder = found[0]["id"] if found else s.files().create(
            body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()["id"]
        _svc[lid] = (s, folder)
    return _svc[lid]


def upload(lid: int, path: Path, name: str) -> str:
    from googleapiclient.http import MediaIoBaseUpload
    for attempt in (0, 1):
        try:
            s, folder = _service(lid)
            with open(path, "rb") as f:  # MediaFileUpload keeps the file open, and Windows then refuses to delete it
                media = MediaIoBaseUpload(f, mimetype="application/octet-stream", resumable=True, chunksize=8 * 1024 * 1024)
                return s.files().create(body={"name": name, "parents": [folder]}, media_body=media, fields="id").execute()["id"]
        except PermissionError:
            raise
        except Exception:
            _svc.pop(lid, None)  # stale service or deleted folder: rebuild once
            if attempt:
                raise


def stream(lid: int, file_id: str):
    """Yield the file in chunks; the browser never gets a Drive URL."""
    from googleapiclient.http import MediaIoBaseDownload
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, _service(lid)[0].files().get_media(fileId=file_id), chunksize=4 * 1024 * 1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
        yield buf.getvalue(); buf.seek(0); buf.truncate()


def delete(lid: int, file_id: str):
    try:
        _service(lid)[0].files().delete(fileId=file_id).execute()
    except Exception:  # already gone / access revoked: nothing more we can do
        pass
