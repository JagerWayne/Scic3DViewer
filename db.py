"""SQLite persistence + license/tier rules. Everything lives under SCIC_DATA (default ./data)."""
import calendar, hashlib, os, re, secrets, sqlite3, time
from pathlib import Path

DATA = Path(os.environ.get("SCIC_DATA", Path(__file__).parent / "data"))
SHARES = DATA / "shares"
MB = 1024 * 1024
SESSION_TTL = 30 * 86400
# The admin is identified by a Google-VERIFIED email (login is Google only), so it cannot be spoofed by typing it.
ADMIN_EMAIL = os.environ.get("SCIC_ADMIN_EMAIL", "waynescicluna.1985@gmail.com").strip().lower()

# Server-side tier table: the single source of truth, also sent to the UI via /api/me.
TIERS = {
    "FREE": dict(max_mb=15, max_shares_month=30, max_annotations=2, share_permissions=False, formats=[],
                 tools=["paint_basic"],
                 images=dict(presets=["light"], custom=False, watermark="forced", max_az=36, max_el=3, max_px=800, max_mb=12)),
    "PRO": dict(max_mb=100, max_shares_month=150, max_annotations=None, share_permissions=True, formats=["glb"],
                tools=["paint_basic", "paint_fill", "assembly", "tour", "slicer", "explode",
                       "decimate", "base", "hdri", "capture"],
                images=dict(presets=["light", "standard"], custom=False, watermark="none", max_az=72, max_el=5, max_px=1024, max_mb=60)),
    "ENTERPRISE": dict(max_mb=500, max_shares_month=5000, max_annotations=None, share_permissions=True,
                       formats=["glb", "obj", "stl", "usdz"],
                       tools=["paint_basic", "paint_fill", "paint_undo", "assembly", "tour",
                              "slicer", "explode", "measure", "decimate", "base", "hdri", "capture", "cad_import"],
                       images=dict(presets=["light", "standard", "high"], custom=True, watermark="custom", max_az=180, max_el=12, max_px=2048, max_mb=250)),
}
# Which share flag needs which tool (recipient permissions can't exceed the creator's tier).
SHARE_FLAG_TOOL = {"allow_measure": "measure", "allow_explode": "explode"}


def connect():
    c = sqlite3.connect(DATA / "scic.db")
    c.row_factory = sqlite3.Row
    return c


def init():
    SHARES.mkdir(parents=True, exist_ok=True)
    for p in SHARES.glob("*"):  # leftovers of an upload that was cut off (crash, power loss): models are never kept on this server
        if p.is_file() and p.stat().st_mtime < time.time() - 3600:
            p.unlink(missing_ok=True)
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS licenses(
          id INTEGER PRIMARY KEY, key_hash TEXT UNIQUE NOT NULL, name TEXT,
          email TEXT NOT NULL, tier TEXT NOT NULL, duration_days INTEGER,
          activated_at INTEGER, expires_at INTEGER, revoked INTEGER DEFAULT 0, created_at INTEGER);
        CREATE TABLE IF NOT EXISTS sessions(
          token_hash TEXT PRIMARY KEY, license_id INTEGER NOT NULL, name TEXT, email TEXT, created_at INTEGER);
        CREATE TABLE IF NOT EXISTS shares(
          share_id TEXT PRIMARY KEY, license_id INTEGER NOT NULL, title TEXT, config TEXT,
          allow_download INTEGER, allow_measure INTEGER, allow_explode INTEGER,
          formats TEXT, created_at INTEGER, views INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS drive_tokens(
          license_id INTEGER PRIMARY KEY, token_enc TEXT NOT NULL, connected_at INTEGER);
        CREATE TABLE IF NOT EXISTS activity(
          id INTEGER PRIMARY KEY, ts INTEGER, license_id INTEGER, action TEXT, ip TEXT, detail TEXT);
        """)
        lhave = {r[1] for r in c.execute("PRAGMA table_info(licenses)")}
        for col, typ in (("paused_at", "INTEGER"), ("note", "TEXT")):  # paused: access off, clock frozen; note: admin-only memo
            if col not in lhave:
                c.execute(f"ALTER TABLE licenses ADD COLUMN {col} {typ}")
        if "surname" in lhave:  # older databases: fold the surname into the single "Name & Surname" field, then drop the column
            c.execute("UPDATE licenses SET name = TRIM(COALESCE(name, '') || ' ' || surname) WHERE surname IS NOT NULL AND surname NOT IN ('', '(customer)')")
            try:
                c.execute("ALTER TABLE licenses DROP COLUMN surname")
            except sqlite3.OperationalError:  # SQLite older than 3.35 cannot drop columns: the unused column simply stays
                pass
        have = {r[1] for r in c.execute("PRAGMA table_info(shares)")}
        for col, typ in (("drive_ids", "TEXT"),            # JSON {"glbz": id, "hdr": id}  (legacy: "glb")
                         ("expires_at", "INTEGER"), ("revoked", "INTEGER DEFAULT 0"), ("pw_hash", "TEXT"),
                         ("kind", "TEXT DEFAULT 'model'"),  # 'model' = interactive 3D, 'images' = 360 image pack (no 3D data for visitors)
                         ("wm", "TEXT")):                    # JSON {text, tile, cross}: watermark chosen by the server from the owner's tier
            if col not in have:
                c.execute(f"ALTER TABLE shares ADD COLUMN {col} {typ}")


def h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def delete_license(lid: int):
    """Remove one paid license. The person keeps their account: shares and Drive login move to their Free license."""
    with connect() as c:
        r = c.execute("SELECT email FROM licenses WHERE id=? AND duration_days IS NOT NULL", (lid,)).fetchone()
        if not r:
            return False
        free = c.execute("SELECT id FROM licenses WHERE email=? AND duration_days IS NULL", (r["email"],)).fetchone()
        has_data = c.execute("SELECT (SELECT COUNT(*) FROM shares WHERE license_id=?) + (SELECT COUNT(*) FROM drive_tokens WHERE license_id=?)",
                             (lid, lid)).fetchone()[0]
        if free:
            free = free["id"]
        elif has_data:  # links or a Drive login exist: they move to a Free account instead of disappearing
            c.execute("INSERT INTO licenses(key_hash,name,email,tier,created_at) VALUES(?,?,?,?,?)",
                      (h(secrets.token_hex(16)), r["email"].split("@")[0], r["email"], "FREE", int(time.time())))
            free = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        if free:
            c.execute("UPDATE shares SET license_id=? WHERE license_id=?", (free, lid))
            c.execute("INSERT OR IGNORE INTO drive_tokens SELECT ?, token_enc, connected_at FROM drive_tokens WHERE license_id=?", (free, lid))
        c.execute("DELETE FROM drive_tokens WHERE license_id=?", (lid,))
        c.execute("DELETE FROM sessions WHERE license_id=?", (lid,))
        c.execute("DELETE FROM licenses WHERE id=?", (lid,))
    return True


def license_ids(email: str) -> list:
    """All license rows of one person (free -> paid changes the row; ownership follows the email)."""
    with connect() as c:
        return [r[0] for r in c.execute("SELECT id FROM licenses WHERE email=?", (email.lower(),))] or [-1]


def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    return salt.hex() + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000).hex()


def check_pw(pw: str, stored: str) -> bool:
    salt, digest = stored.split("$")
    return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex(), digest)


def new_key() -> str:
    raw = secrets.token_hex(10).upper()  # 80 bits
    return "SCIC-" + "-".join(raw[i:i + 5] for i in range(0, 20, 5))


def parse_duration(s: str) -> int:
    """'30d', '6m', '2y' -> days (month=30d, year=365d)."""
    m = re.fullmatch(r"\s*(\d{1,4})\s*([dmy])\s*", s.lower())
    if not m:
        raise ValueError("duration must look like 30d, 6m or 1y")
    return int(m[1]) * {"d": 1, "m": 30, "y": 365}[m[2]]


def create_license(name, email, tier, duration) -> str:  # name = "Name & Surname"
    tier = tier.upper()
    if tier not in TIERS:
        raise ValueError("tier must be one of " + ", ".join(TIERS))
    key = new_key()
    with connect() as c:
        c.execute("INSERT INTO licenses(key_hash,name,email,tier,duration_days,created_at) VALUES(?,?,?,?,?,?)",
                  (h(key), name, email.lower(), tier, parse_duration(duration), int(time.time())))
    return key


def effective_tier(lic) -> str:
    if lic["revoked"] or lic["paused_at"] or (lic["expires_at"] and lic["expires_at"] < time.time()):
        return "FREE"
    return lic["tier"]


def shares_this_month(email: str) -> int:
    """New links published by this person since the 1st of the (UTC) month. Updating a link in place and deleting one
    do not change the count: it comes from the activity log, so deleting links cannot be used to publish more."""
    t = time.gmtime()
    start = calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0))
    ids = license_ids(email)
    with connect() as c:
        return c.execute(f"SELECT COUNT(*) FROM activity WHERE action='share' AND ts>=? AND license_id IN ({','.join('?' * len(ids))})",
                         (start, *ids)).fetchone()[0]


def log(license_id, action, ip="", detail=""):
    with connect() as c:
        c.execute("INSERT INTO activity(ts,license_id,action,ip,detail) VALUES(?,?,?,?,?)",
                  (int(time.time()), license_id, action, ip, detail[:200]))


def login_google(name, email, ip):
    """Called with a Google-VERIFIED email. The license issued for that email applies (the clock starts at the first
    sign-in); otherwise a Free license. Returns (session_token, license_row)."""
    email, now = email.strip().lower(), int(time.time())
    name = (name or email.split("@")[0]).strip()[:80]
    with connect() as c:
        lic = c.execute("SELECT * FROM licenses WHERE email=? AND revoked=0 AND paused_at IS NULL AND duration_days IS NOT NULL "
                        "AND (expires_at IS NULL OR expires_at>?) ORDER BY id DESC", (email, now)).fetchone()
        if lic and not lic["activated_at"]:  # duration starts counting at first activation
            c.execute("UPDATE licenses SET activated_at=?, expires_at=? WHERE id=?",
                      (now, now + lic["duration_days"] * 86400, lic["id"]))
        if not lic:  # no valid paid license: the (single) Free license of this email
            lic = c.execute("SELECT * FROM licenses WHERE email=? AND duration_days IS NULL", (email,)).fetchone()
        if not lic:
            c.execute("INSERT INTO licenses(key_hash,name,email,tier,created_at) VALUES(?,?,?,?,?)",
                      (h(secrets.token_hex(16)), name, email, "FREE", now))
            lic = c.execute("SELECT * FROM licenses WHERE rowid=last_insert_rowid()").fetchone()
        # keep the user's Google Drive connection when their license row changes (free -> paid, renewal)
        c.execute("INSERT OR IGNORE INTO drive_tokens SELECT ?, token_enc, connected_at FROM drive_tokens "
                  "WHERE license_id IN (SELECT id FROM licenses WHERE email=?) LIMIT 1", (lic["id"], email))
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (h(token), lic["id"], name, email, now))
        lic = c.execute("SELECT * FROM licenses WHERE id=?", (lic["id"],)).fetchone()
    log(lic["id"], "login", ip, email)
    return token, lic


def session_user(token: str):
    """Return dict(name,email,license_id,tier,expires_at) or None."""
    with connect() as c:
        r = c.execute("SELECT s.*, l.tier, l.revoked, l.expires_at, l.paused_at FROM sessions s JOIN licenses l "
                      "ON l.id=s.license_id WHERE token_hash=?", (h(token),)).fetchone()
    if not r or r["created_at"] < time.time() - SESSION_TTL:
        return None
    admin = r["email"] == ADMIN_EMAIL
    return dict(name=r["name"], email=r["email"], license_id=r["license_id"], is_admin=admin,
                tier="ENTERPRISE" if admin else effective_tier(r), expires_at=None if admin else r["expires_at"])
