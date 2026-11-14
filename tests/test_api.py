import gzip, hashlib, hmac, io, itertools, json, os, tempfile, time
os.environ["SCIC_DATA"] = tempfile.mkdtemp()
import pytest
from fastapi.testclient import TestClient
from PIL import Image
import app as appmod, billing, db, mailer, storage

c = TestClient(appmod.app)
real_upload = storage.upload  # the fixture below replaces storage.upload with a fake
cloud = {}
ids = itertools.count(1)
GLB = b"glTF" + b"\0" * 100


@pytest.fixture(autouse=True)
def fake_drive(monkeypatch):  # in-memory per-user "Drive"; every test user counts as connected
    cloud.clear(); db.clear_rate_limits()  # each test starts with fresh rate-limit counters
    monkeypatch.setattr(storage, "configured", lambda: True)
    monkeypatch.setattr(storage, "connected", lambda lid: True)
    def upload(lid, p, name, progress=None):  # unique ids, like the real Drive
        fid = f"{lid}:{name}:{next(ids)}"; cloud[fid] = p.read_bytes()
        if progress:
            progress(len(cloud[fid]) // 2, len(cloud[fid])); progress(len(cloud[fid]), len(cloud[fid]))
        return fid
    monkeypatch.setattr(storage, "upload", upload)
    monkeypatch.setattr(storage, "stream", lambda lid, fid: iter([cloud[fid]]))
    monkeypatch.setattr(storage, "delete", lambda lid, fid: cloud.pop(fid, None))


def login(email):  # what the Google callback does once Google has verified the email
    return {"Authorization": "Bearer " + db.login_google("Ann", email, "test")[0]}


_IMG = {}


def _pic(px, fmt, color=(30, 60, 90, 255)):
    if (px, fmt) not in _IMG:
        buf = io.BytesIO()
        im = Image.new("RGBA", (px, px), color)
        (im if fmt == "WEBP" else im.convert("RGB")).save(buf, fmt)
        _IMG[(px, fmt)] = buf.getvalue()
    return _IMG[(px, fmt)]


def mkpack(az=4, el=1, px=64, mime="image/webp", anno=None, thumbs=True):
    fr = _pic(px, "WEBP" if mime == "image/webp" else "JPEG"); n = az * el
    frames = [[i * len(fr), len(fr)] for i in range(n)]
    ix = {"v": 1, "mime": mime, "w": px, "h": px, "az": az, "el": el, "elevs": [20] * el, "frames": frames, "anno": anno}
    data = fr * n
    if thumbs:
        tb = _pic(min(px, 32), "WEBP" if mime == "image/webp" else "JPEG")
        ix["thumbs"] = [[len(data) + i * len(tb), len(tb)] for i in range(n)]
        data += tb * n
    head = json.dumps(ix).encode()
    return b"S3IM" + len(head).to_bytes(4, "little") + head + data


def share(h, pack=None, model=False, **flags):  # a picture link; the model is only sent when asked for (or for a download)
    files = {"pack": ("p.s3im", pack or mkpack())}
    if model or "allow_download" in flags:
        files["model"] = ("m.glb", GLB)
    return c.post("/api/projects/share", headers=h, files=files, data={"config": "{}", **{k: "true" for k in flags}})


def share_images(h, pack=None, **data):  # always with the model
    return c.post("/api/projects/share", headers=h, files={"model": ("m.glb", GLB), "pack": ("p.s3im", pack or mkpack())},
                  data={"config": "{}", "kind": "images", **data})


def job_of(h, r):
    return c.get(f"/api/jobs/{r.json()['job']}", headers=h).json()


def tier(h):
    return c.get("/api/me", headers=h).json()["tier"]


def test_free_flow_and_gating():
    h = login("free@x.com")
    assert tier(h) == "FREE"
    assert share(h, allow_download=True).status_code == 403   # custom perms need PRO
    sid = share(h).json()["share_id"]
    assert c.get(f"/api/projects/{sid}/model").status_code == 404   # no model download on Free
    assert c.get(f"/api/projects/{sid}/config").json()["allow_download"] is False


def test_license_follows_the_google_email():
    db.create_license("Bob B", "pro@x.com", "PRO", "30d")
    assert tier(login("other@x.com")) == "FREE"                    # someone else's email gets nothing
    h = login("PRO@x.com")                                         # case-insensitive match, clock starts now
    me = c.get("/api/me", headers=h).json()
    assert me["tier"] == "PRO" and me["expires_at"]
    assert share(h, allow_download=True).status_code == 200
    with db.connect() as con:
        con.execute("UPDATE licenses SET expires_at=1 WHERE email='pro@x.com'")
    assert tier(h) == "FREE" and tier(login("pro@x.com")) == "FREE"  # expired -> falls back, also on next sign-in
    assert len({db.login_google("A", "free2@x.com", "t")[1]["id"] for _ in range(3)}) == 1  # one free license per email


def test_auth_and_validation():
    assert c.post("/api/projects/share", files={"pack": ("p.s3im", mkpack())}).status_code == 401
    assert c.post("/api/register", json={}).status_code in (404, 405)   # old form login is gone
    h = login("v@x.com")
    assert c.post("/api/projects/share", headers=pro("v2@x.com"), files={"model": ("m.glb", b"nope"), "pack": ("p.s3im", mkpack())}, data={"allow_download": "true"}).status_code == 400
    assert c.post("/api/projects/share", headers=h, files={"model": ("m.glb", GLB)}).status_code == 400   # no pictures
    assert "nonce-" in c.get("/studio").headers["content-security-policy"]


def test_drive_only_storage(monkeypatch):
    h = login("drive@x.com")
    sid = share(h).json()["share_id"]
    assert not list(db.SHARES.glob("*")) and cloud                     # nothing stays on local disk
    assert c.get(f"/api/projects/{sid}/images").content.startswith(b"S3IM")  # streamed back through the server
    assert c.delete(f"/api/projects/{sid}", headers=h).status_code == 200 and not cloud
    monkeypatch.setattr(storage, "upload", lambda *a: 1 / 0)           # Drive failing -> error, nothing left behind
    r = share(h)                                                       # the slow part runs in a background job: the request itself succeeds...
    assert r.status_code == 200 and not list(db.SHARES.glob("*"))
    j = job_of(h, r)
    assert j["done"] and "Google Drive failed" in j["error"] and "Failed" in j["log"][-1][2]   # ...and the job reports what went wrong
    assert c.get("/api/projects", headers=h).json() == []              # nothing half-published
    monkeypatch.setattr(storage, "connected", lambda lid: False)       # user has not connected Drive -> refused
    assert share(h).status_code == 409 and not list(db.SHARES.glob("*"))


def test_each_user_has_own_drive_and_oauth_state():
    a, b = login("a@x.com"), login("b@x.com")
    sid = share(a).json()["share_id"]
    lid_a = db.session_user(a["Authorization"][7:])["license_id"]
    assert list(cloud)[0].startswith(f"{lid_a}:")                      # uploaded with A's own connection
    assert c.delete(f"/api/projects/{sid}", headers=b).status_code == 403
    r = c.get("/auth/google/callback?state=forged&code=x", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("drive=error")  # unknown state is rejected
    assert c.post("/api/drive/connect").status_code == 401


def test_google_login_flow(monkeypatch):
    monkeypatch.setattr(storage, "configured", lambda: False)
    r = c.get("/auth/google/login", follow_redirects=False)
    assert r.headers["location"] == "/studio?login=unavailable"        # site owner has not set up Google yet
    monkeypatch.setattr(storage, "purpose", lambda s: "login")
    monkeypatch.setattr(storage, "finish", lambda s, code: ("login", None, {"email": "G@x.com", "name": "Gina"}))
    r = c.get("/auth/google/callback?state=s&code=x", follow_redirects=False)
    loc = r.headers["location"]
    assert loc.startswith("/studio#token=")                            # token in the fragment, not the query
    me = c.get("/api/me", headers={"Authorization": "Bearer " + loc.split("=", 1)[1]}).json()
    assert me["email"] == "g@x.com" and me["name"] == "Gina"
    monkeypatch.setattr(storage, "finish", lambda *a: 1 / 0)           # Google failure -> back to the page with an error
    assert c.get("/auth/google/callback?state=s&code=x", follow_redirects=False).headers["location"] == "/studio?login=error"


def test_login_asks_for_drive_on_the_same_google_screen(monkeypatch, tmp_path):
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"web": {"client_id": "cid", "client_secret": "s", "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                                          "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://x/auth/google/callback"]}}))
    monkeypatch.setattr(storage, "CLIENT", client)
    url = storage.begin("login")
    assert "drive.file" in url and "userinfo.email" in url and "access_type=offline" in url   # one consent screen for both
    saved = {}
    monkeypatch.setattr(storage, "save_token", lambda lid, tok: saved.update({lid: tok}))
    monkeypatch.setattr(storage, "purpose", lambda s: "login")
    monkeypatch.setattr(storage, "finish", lambda s, code: ("login", None, {"email": "d1@x.com", "name": "D", "drive_token": "TOK"}))
    c.get("/auth/google/callback?state=s&code=x", follow_redirects=False)
    assert list(saved.values()) == ["TOK"]                                                    # Drive allowed: connected at sign-in
    saved.clear()
    monkeypatch.setattr(storage, "finish", lambda s, code: ("login", None, {"email": "d2@x.com", "name": "D"}))
    assert c.get("/auth/google/callback?state=s&code=x", follow_redirects=False).headers["location"].startswith("/studio#token=")
    assert not saved                                                                          # Drive unticked: signed in, not connected


def test_drive_follows_license_change():
    h = login("mv@x.com"); lid_free = db.session_user(h["Authorization"][7:])["license_id"]
    with db.connect() as con:
        con.execute("INSERT INTO drive_tokens VALUES(?,?,?)", (lid_free, "enc", 1))
    db.create_license("M V", "mv@x.com", "PRO", "30d")
    lid_pro = db.session_user(login("mv@x.com")["Authorization"][7:])["license_id"]
    assert lid_pro != lid_free
    with db.connect() as con:
        assert con.execute("SELECT 1 FROM drive_tokens WHERE license_id=?", (lid_pro,)).fetchone()  # connection carried over


def test_homepage_meets_google_verification_rules():
    home = c.get("/")                                                      # public, no login, plain server-rendered HTML
    assert home.status_code == 200 and "<title>Scic3DViewer</title>" in home.text and "<h1>Scic3DViewer</h1>" in home.text
    assert 'href="/privacy"' in home.text and 'href="/terms"' in home.text  # visible links to the legal pages
    assert "web application for opening, editing, annotating and measuring 3D models" in home.text  # states the purpose
    assert "drive.file" in home.text and "{{" not in home.text
    assert c.get("/privacy").text != home.text                             # the privacy policy is a different page than the homepage
    privacy = c.get("/privacy").text
    for scope in ("openid", "userinfo.email", "userinfo.profile", "drive.file"):
        assert scope in privacy
    assert "Scic3DViewer Privacy Policy" in privacy and "Scic3DViewer" in c.get("/studio").text


def test_legal_pages_are_public_and_filled_in():
    for path, title in (("/privacy", "Privacy Policy"), ("/terms", "Terms of Service")):
        r = c.get(path)
        assert r.status_code == 200 and title in r.text
        assert "{{" not in r.text and appmod.CONTACT in r.text           # placeholders were replaced
    assert "drive.file" in c.get("/privacy").text and "Google API Services User Data Policy" in c.get("/privacy").text


def test_old_upload_leftovers_are_removed_at_start():
    old, fresh = db.SHARES / "leftover-old.glb.gz", db.SHARES / "leftover-new.glb"
    old.write_bytes(b"x"); fresh.write_bytes(b"x")
    two_hours_ago = time.time() - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))
    db.init()
    assert not old.exists() and fresh.exists()                 # only stale files go: an upload in progress is left alone
    fresh.unlink()


def test_drive_help_is_public():
    r = c.get("/help/drive")  # no login needed
    assert r.status_code == 200 and "Connect Google Drive" in r.text


def test_token_encryption_roundtrip():
    f = storage._fernet()
    assert f.decrypt(f.encrypt(b"refresh")) == b"refresh"


def test_upload_releases_the_local_file(monkeypatch, tmp_path):  # Windows cannot delete a file that is still open
    p = tmp_path / "a.glb"; p.write_bytes(GLB)

    class Files:
        def create(self, body, media_body, fields): self.m = media_body; return self
        def next_chunk(self): self.m.getbytes(0, 4); return None, {"id": "x"}

    class Svc:
        def files(self): return Files()

    monkeypatch.setattr(storage, "_service", lambda lid: (Svc(), "folder"))
    assert real_upload(1, p, "a.glb") == "x"
    p.unlink()


def pro(email):
    db.create_license("P P", email, "PRO", "30d")
    return login(email)


def test_link_password_expiry_revoke():
    h = pro("pro2@x.com")
    r = c.post("/api/projects/share", headers=h, files={"model": ("m.glb", GLB), "pack": ("p.s3im", mkpack())},
               data={"config": "{}", "password": "secret1", "expires_in_days": "7", "allow_download": "true"})
    assert r.status_code == 200
    sid = r.json()["share_id"]
    assert c.get(f"/api/projects/{sid}/config").status_code == 401
    assert c.get(f"/api/projects/{sid}/config", headers={"X-Share-Password": "nope"}).status_code == 401
    ok = {"X-Share-Password": "secret1"}
    assert c.get(f"/api/projects/{sid}/config", headers=ok).status_code == 200
    assert c.get(f"/api/projects/{sid}/model", headers=ok).content == GLB
    assert c.get(f"/api/projects/{sid}/edit-model", headers=h).content == GLB       # the owner never needs the password
    assert c.patch(f"/api/projects/{sid}", headers=h, json={"revoked": True}).status_code == 200
    assert c.get(f"/api/projects/{sid}/config", headers=ok).status_code == 410
    assert c.patch(f"/api/projects/{sid}", headers=h, json={"revoked": False, "password": ""}).status_code == 200
    assert c.get(f"/api/projects/{sid}/config").status_code == 200                  # password removed
    with db.connect() as con:
        con.execute("UPDATE shares SET expires_at=1 WHERE share_id=?", (sid,))
    assert c.get(f"/api/projects/{sid}/model").status_code == 410                   # expired


def test_free_cannot_use_link_controls():
    h = login("free3@x.com")
    for extra in ({"password": "secret1"}, {"expires_in_days": "3"}):
        r = c.post("/api/projects/share", headers=h, files={"pack": ("p.s3im", mkpack())}, data={"config": "{}", **extra})
        assert r.status_code == 403
    sid = share(h).json()["share_id"]
    assert c.patch(f"/api/projects/{sid}", headers=h, json={"password": "secret1"}).status_code == 403


def test_projects_list_update_in_place_and_gzip():
    h = pro("pro3@x.com")
    sid = share(h, model=True).json()["share_id"]
    assert sorted(k.split(":")[1].rsplit(".", 1)[1] for k in cloud) == ["gz", "pack"]  # the model is stored compressed in Drive
    assert gzip.decompress(next(v for k, v in cloud.items() if ".glb.gz" in k)) == GLB
    assert [p["share_id"] for p in c.get("/api/projects", headers=h).json()] == [sid]
    assert c.get("/api/projects", headers=login("nobody@x.com")).json() == []
    old = set(cloud)
    r = c.post("/api/projects/share", headers=h, files={"pack": ("p.s3im", mkpack())}, data={"config": "{}", "update_id": sid})
    assert r.json()["share_id"] == sid and len(cloud) == 1 and not (old & set(cloud))  # same address, old files removed
    assert c.post("/api/projects/share", headers=login("evil@x.com"), files={"pack": ("p.s3im", mkpack())},
                  data={"config": "{}", "update_id": sid}).status_code == 403
    assert c.delete(f"/api/projects/{sid}", headers=h).status_code == 200 and c.get("/api/projects", headers=h).json() == []


def test_viewer_can_be_embedded_but_nothing_else():
    v, s = c.get("/v/abcdefghij"), c.get("/studio")
    assert "frame-ancestors *" in v.headers["content-security-policy"] and "x-frame-options" not in v.headers
    assert "frame-ancestors 'none'" in s.headers["content-security-policy"] and s.headers["x-frame-options"] == "DENY"


def test_admin_panel(monkeypatch):
    assert c.get("/api/admin/stats").status_code == 401
    assert c.get("/api/admin/stats", headers=login("someone@x.com")).status_code == 403
    adm = login("WayneScicluna.1985@gmail.com")                                       # same Google account, any letter case
    me = c.get("/api/me", headers=adm).json()
    assert me["is_admin"] and me["tier"] == "ENTERPRISE" and not c.get("/api/me", headers=login("x9@x.com")).json()["is_admin"]
    new = {"name": "Zed Z", "email": "zed@x.com", "tier": "PRO", "duration": "30d"}
    assert c.post("/api/admin/licenses", headers=adm, json=new).status_code == 200
    assert c.post("/api/admin/licenses", headers=adm, json={**new, "tier": "GOLD"}).status_code == 400
    assert c.post("/api/admin/licenses", headers=adm, json={**new, "email": "nope"}).status_code == 422
    sent = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subj, body: sent.append(to))
    r = c.post("/api/admin/licenses", headers=adm, json={**new, "email": "mail@x.com", "send_email": True}).json()
    assert r["emailed"] is True and sent == ["mail@x.com"]
    lic = next(x for x in c.get("/api/admin/licenses", headers=adm).json() if x["email"] == "zed@x.com")
    assert lic["status"] == "pending" and lic["duration_days"] == 30
    assert c.patch(f"/api/admin/licenses/{lic['id']}", headers=adm, json={"extend_days": 30}).status_code == 200
    assert c.patch(f"/api/admin/licenses/{lic['id']}", headers=adm, json={"revoked": True}).status_code == 200
    again = next(x for x in c.get("/api/admin/licenses", headers=adm).json() if x["id"] == lic["id"])
    assert again["status"] == "revoked" and again["duration_days"] == 60
    assert tier(login("zed@x.com")) == "FREE"                                          # revoked license does nothing
    sid = share(login("owner9@x.com")).json()["share_id"]
    assert any(row["share_id"] == sid for row in c.get("/api/admin/shares", headers=adm).json())
    assert c.patch(f"/api/admin/shares/{sid}", headers=adm, json={"revoked": True}).status_code == 200
    assert c.get(f"/api/projects/{sid}/config").status_code == 410
    assert c.delete(f"/api/admin/shares/{sid}", headers=adm).status_code == 200 and not cloud
    st = c.get("/api/admin/stats", headers=adm).json()
    assert st["config"]["admin"] == "waynescicluna.1985@gmail.com" and st["people"] >= 3 and st["daily"]
    assert any(a["action"] == "admin:license" for a in c.get("/api/admin/activity?q=admin", headers=adm).json())
    assert c.get("/admin").status_code == 200                                          # static page; the API is what is protected


def test_admin_edits_and_pauses_licenses():
    adm = login(db.ADMIN_EMAIL)
    c.post("/api/admin/licenses", headers=adm, json={"name": "Ed It", "email": "ed@x.com", "tier": "PRO", "duration": "30d"})
    rows = lambda: c.get("/api/admin/licenses", headers=adm).json()
    lic = lambda email, paid=True: next(x for x in rows() if x["email"] == email and x["managed"] == paid)
    lid = lic("ed@x.com")["id"]
    patch = lambda lid_=None, **b: c.patch(f"/api/admin/licenses/{lid_ or lid}", headers=adm, json=b)
    assert patch(name="Edward Itt", note="vip", duration_days=90, tier="ENTERPRISE").status_code == 200
    l = lic("ed@x.com")
    assert (l["name"], l["note"], l["duration_days"], l["tier"], l["status"]) == ("Edward Itt", "vip", 90, "ENTERPRISE", "pending")
    assert "surname" not in l                                                     # one field: "Name & Surname"
    assert patch(expires_on="2030-01-31").status_code == 400                      # the clock has not started yet
    h = login("ed@x.com")
    assert tier(h) == "ENTERPRISE" and lic("ed@x.com")["status"] == "active"      # the first sign-in starts the clock
    assert patch(duration_days=5).status_code == 400                              # now the expiry date is what counts
    assert patch(expires_on="2030-01-31").status_code == 200
    assert time.strftime("%Y-%m-%d", time.localtime(lic("ed@x.com")["expires_at"])) == "2030-01-31"
    before = lic("ed@x.com")["expires_at"]
    assert patch(paused=True).status_code == 200
    assert lic("ed@x.com")["status"] == "paused" and tier(h) == "FREE" and tier(login("ed@x.com")) == "FREE"
    with db.connect() as con:                                                     # pretend the pause started an hour ago
        con.execute("UPDATE licenses SET paused_at=paused_at-3600 WHERE id=?", (lid,))
    assert patch(paused=False).status_code == 200
    after = lic("ed@x.com")
    assert after["status"] == "active" and 3590 <= after["expires_at"] - before <= 3700 and tier(h) == "ENTERPRISE"  # the pause cost no time
    assert patch(email="nope").status_code == 422
    assert patch(email="Ed2@x.com").status_code == 200                            # moving a license to another Google account
    assert tier(login("ed2@x.com")) == "ENTERPRISE" and tier(login("ed@x.com")) == "FREE"
    free = lic("ed@x.com", paid=False)
    assert patch(free["id"], name="Just a name", note="n").status_code == 200
    assert patch(free["id"], tier="PRO").status_code == 400                       # free accounts are automatic
    assert patch(99999, name="x").status_code == 404


def test_admin_gives_a_license_to_an_existing_free_account():
    adm = login(db.ADMIN_EMAIL)
    h = login("gift@x.com")                                                       # someone who simply signed in: Free
    assert tier(h) == "FREE"
    rows = lambda: c.get("/api/admin/licenses", headers=adm).json()
    row = next(x for x in rows() if x["email"] == "gift@x.com")
    assert row["managed"] is False and row["status"] == "free"
    patch = lambda **b: c.patch(f"/api/admin/licenses/{row['id']}", headers=adm, json=b)
    assert patch(tier="PRO").status_code == 400                                   # a length is needed
    assert patch(email="other@x.com").status_code == 400                          # the identity of an automatic account is fixed
    assert patch(revoked=True).status_code == 400
    assert patch(tier="PRO", duration_days=30).status_code == 200                 # the whole "give a license" step
    assert tier(h) == "PRO"                                                       # applies to the open session at once
    row = next(x for x in rows() if x["email"] == "gift@x.com")
    assert row["managed"] and row["status"] == "active" and 29 * 86400 < row["expires_at"] - time.time() <= 30 * 86400
    assert patch(tier="ENTERPRISE").status_code == 200 and tier(h) == "ENTERPRISE"
    assert patch(paused=True).status_code == 200 and tier(h) == "FREE"
    assert patch(paused=False, revoked=True).status_code == 200 and tier(h) == "FREE"
    assert patch(revoked=False).status_code == 200 and tier(h) == "ENTERPRISE"    # every status can be changed back
    assert patch(start_now=True).status_code == 400                               # it has already started


def test_surname_is_folded_into_the_name_when_an_old_database_is_upgraded(monkeypatch, tmp_path):
    import sqlite3
    monkeypatch.setattr(db, "DATA", tmp_path)
    con = sqlite3.connect(tmp_path / "scic.db")
    con.execute("CREATE TABLE licenses(id INTEGER PRIMARY KEY, key_hash TEXT UNIQUE NOT NULL, name TEXT, surname TEXT, email TEXT NOT NULL, "
                "tier TEXT NOT NULL, duration_days INTEGER, activated_at INTEGER, expires_at INTEGER, revoked INTEGER DEFAULT 0, created_at INTEGER)")
    con.executemany("INSERT INTO licenses(key_hash,name,surname,email,tier,duration_days) VALUES(?,?,?,?,?,?)",
                    [("a", "Ada", "Lovelace", "a@x.com", "PRO", 30), ("b", "Stripe Buyer", "(customer)", "b@x.com", "PRO", 30), ("c", "Free Person", None, "c@x.com", "FREE", None)])
    con.commit(); con.close()
    db.init()
    with db.connect() as con:
        assert {r["email"]: r["name"] for r in con.execute("SELECT * FROM licenses")} == {"a@x.com": "Ada Lovelace", "b@x.com": "Stripe Buyer", "c@x.com": "Free Person"}
        cols = [r[1] for r in con.execute("PRAGMA table_info(licenses)")]
        assert "surname" not in cols or sqlite3.sqlite_version_info < (3, 35)     # old SQLite cannot drop the column, it just stays unused


def test_admin_deletes_a_license_or_a_whole_person():
    adm = login(db.ADMIN_EMAIL)
    rows = lambda: c.get("/api/admin/licenses", headers=adm).json()
    for email in ("keep@x.com", "gone@x.com"):
        c.post("/api/admin/licenses", headers=adm, json={"name": "N S", "email": email, "tier": "PRO", "duration": "30d"})
    paid = lambda email: next(x["id"] for x in rows() if x["email"] == email and x["managed"])
    # delete only the license: the person keeps the account, the link and the Drive file
    keep = login("keep@x.com"); sid = share(keep).json()["share_id"]
    assert c.delete(f"/api/admin/licenses/{paid('keep@x.com')}", headers=adm).status_code == 200
    assert not any(x["email"] == "keep@x.com" and x["managed"] for x in rows())
    assert c.get("/api/me", headers=keep).status_code == 401                      # the session of the deleted license ended
    again = login("keep@x.com")
    assert tier(again) == "FREE" and [p["share_id"] for p in c.get("/api/projects", headers=again).json()] == [sid] and cloud
    # delete the whole person: links, Drive files, sessions, licenses
    gone = login("gone@x.com"); share(gone); share(gone); n_before = len(cloud)
    assert c.delete(f"/api/admin/licenses/{paid('gone@x.com')}?purge=true", headers=adm).status_code == 200
    assert len(cloud) == n_before - 2                                             # their two files left the Drive
    assert c.get("/api/me", headers=gone).status_code == 401 and not any(x["email"] == "gone@x.com" for x in rows())
    # a free account cannot be removed without the explicit purge
    login("onlyfree@x.com")
    free_id = next(x["id"] for x in rows() if x["email"] == "onlyfree@x.com")
    assert c.delete(f"/api/admin/licenses/{free_id}", headers=adm).status_code == 400
    assert c.delete(f"/api/admin/licenses/{free_id}?purge=true", headers=adm).status_code == 200
    # the admin account is protected
    admin_row = next(x for x in rows() if x["email"] == db.ADMIN_EMAIL)
    assert c.delete(f"/api/admin/licenses/{admin_row['id']}?purge=true", headers=adm).status_code == 400
    assert c.delete("/api/admin/licenses/99999", headers=adm).status_code == 404


def test_mailer_needs_configuration(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(RuntimeError):
        mailer.send("a@x.com", "s", "b")


def test_stripe_webhook_creates_one_license(monkeypatch):
    monkeypatch.setattr(billing, "HOOK", "whsec_test"); monkeypatch.setattr(billing, "KEY", "sk_test")
    monkeypatch.setattr(billing, "PRICES", {"PRO:1m": "price_1"})
    assert c.get("/api/billing/plans").json() == [{"id": "PRO:1m", "tier": "PRO", "duration": "1m"}]
    body = json.dumps({"type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "payment_status": "paid", "metadata": {"plan": "PRO:1m", "email": "buyer@x.com"}, "customer_details": {"name": "Buyer"}}}}).encode()
    sign = lambda t: hmac.new(b"whsec_test", f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    t = int(time.time())
    assert c.post("/api/billing/webhook", content=body, headers={"Stripe-Signature": f"t={t},v1=bad"}).status_code == 400
    assert c.post("/api/billing/webhook", content=body, headers={"Stripe-Signature": f"t={t - 1000},v1={sign(t - 1000)}"}).status_code == 400  # replay window
    good = {"Stripe-Signature": f"t={t},v1={sign(t)}"}
    assert c.post("/api/billing/webhook", content=body, headers=good).status_code == 200
    assert c.post("/api/billing/webhook", content=body, headers=good).status_code == 200      # Stripe retries
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM licenses WHERE email='buyer@x.com'").fetchone()[0] == 1
    assert tier(login("buyer@x.com")) == "PRO"


# ---------- 360 picture links: visitors get pictures only ----------
def test_image_link_never_sends_the_model():
    h = pro("img1@x.com")
    sid = share_images(h).json()["share_id"]
    cfg = c.get(f"/api/projects/{sid}/config").json()
    assert cfg["kind"] == "images" and cfg["watermark"] is None                      # Pro: no watermark
    assert c.get(f"/api/projects/{sid}/images").content.startswith(b"S3IM")
    assert c.get(f"/api/projects/{sid}/model").status_code == 404                     # download off: the model is not served
    assert c.get(f"/api/projects/{sid}/edit-model", headers=h).content == GLB       # the owner can still edit it
    sid2 = share_images(h, allow_download="true").json()["share_id"]
    assert c.get(f"/api/projects/{sid2}/model").content == GLB                       # Pro may offer the model download
    assert c.get(f"/api/projects/{sid2}/config").json()["formats"] == ["glb"]
    assert share_images(h, kind="model").status_code == 400                           # interactive 3D links no longer exist


def test_image_links_are_gated_by_tier():
    free = login("img2@x.com")
    r = share_images(free)
    assert r.status_code == 200
    cfg = c.get(f"/api/projects/{r.json()['share_id']}/config").json()
    assert cfg["watermark"] == {"text": "Scic3DViewer", "tile": True, "cross": True, "baked": True}  # Free: watermark and faint cross, set by the server
    assert share_images(free, watermark="mine").status_code == 200                    # the typed text is ignored for Free
    assert share_images(free, allow_download="true").status_code == 403               # Free: no model download
    assert share_images(free, pack=mkpack(az=72, el=3)).status_code == 400            # Light only (36 shots)
    assert share_images(free, pack=mkpack(px=1024)).status_code == 400
    pro_h = pro("img3@x.com")
    assert share_images(pro_h, pack=mkpack(az=72, el=5, px=1024)).status_code == 200  # Standard
    assert share_images(pro_h, pack=mkpack(az=120, el=7, px=1400)).status_code == 400 # High is Enterprise
    sid = share_images(pro_h, watermark="x").json()["share_id"]
    assert c.get(f"/api/projects/{sid}/config").json()["watermark"] is None


def test_enterprise_fine_tunes_and_sets_its_own_watermark():
    db.create_license("E E", "img4@x.com", "ENTERPRISE", "30d")
    h = login("img4@x.com")
    r = share_images(h, pack=mkpack(az=12, el=2, px=128, anno=[[]] * 24), watermark="(c) Studio")
    assert r.status_code == 200
    cfg = c.get(f"/api/projects/{r.json()['share_id']}/config").json()
    assert cfg["watermark"] == {"text": "(c) Studio", "tile": False, "cross": False, "baked": True}
    assert share_images(h, pack=mkpack(az=120, el=7, px=1400, anno=[[]] * 840)).status_code == 200  # the High preset fits Enterprise (no watermark text: not re-encoded)
    assert share_images(h, pack=mkpack(az=181, el=1)).status_code == 400              # beyond the Enterprise ceiling
    assert share_images(h, pack=mkpack(az=3, el=1, anno=[[]])).status_code == 400    # anno list must match the frame count


def test_bad_image_packs_are_refused():
    h = pro("img5@x.com")
    for bad in (b"nope" * 10, b"S3IM" + (9).to_bytes(4, "little") + b"not json!", mkpack(mime="image/gif")):
        assert share_images(h, pack=bad).status_code == 400
    r = c.post("/api/projects/share", headers=h, files={"model": ("m.glb", GLB)}, data={"config": "{}", "kind": "images"})
    assert r.status_code == 400                                                       # no pack at all
    assert not list(db.SHARES.glob("*.pack"))                                          # nothing left on this server


def test_picture_link_stores_the_model_only_when_downloads_are_allowed():
    h = pro("img6@x.com")
    post = lambda **d: c.post("/api/projects/share", headers=h, files={"pack": ("p.s3im", mkpack())}, data={"config": "{}", "kind": "images", **d})
    r = post()                                                                        # no download: no model is uploaded at all
    assert r.status_code == 200
    assert not any(".glb.gz" in k for k in cloud) and any(".pack" in k for k in cloud)
    sid = r.json()["share_id"]
    assert c.get(f"/api/projects/{sid}/model").status_code == 404
    assert c.get(f"/api/projects/{sid}/edit-model", headers=h).status_code == 404
    row = next(p for p in c.get("/api/projects", headers=h).json() if p["share_id"] == sid)
    assert row["has_model"] is False and row["kind"] == "images"
    assert post(allow_download="true").status_code == 400                             # downloads need the model file
    assert c.post("/api/projects/share", headers=h, files={"pack": ("p.s3im", mkpack())}, data={"config": "{}", "kind": "model"}).status_code == 400
    sid2 = share_images(h, allow_download="true").json()["share_id"]
    assert next(p for p in c.get("/api/projects", headers=h).json() if p["share_id"] == sid2)["has_model"] is True


def test_monthly_link_limit_per_tier(monkeypatch):
    assert [db.TIERS[t]["max_shares_month"] for t in ("FREE", "PRO", "ENTERPRISE")] == [30, 150, 5000]
    monkeypatch.setitem(db.TIERS["FREE"], "max_shares_month", 2)
    h = login("quota@x.com")
    first = share(h).json()["share_id"]
    assert share(h).status_code == 200
    assert c.get("/api/me", headers=h).json()["shares_month"] == 2
    r = share(h)
    assert r.status_code == 403 and "Monthly limit" in r.json()["detail"]
    assert c.delete(f"/api/projects/{first}", headers=h).status_code == 200            # deleting a link does not give the quota back
    assert share(h).status_code == 403
    r = c.post("/api/projects/share", headers=h, files={"pack": ("p.s3im", mkpack())}, data={"config": "{}", "update_id": share_id_of(h)})
    assert r.status_code == 200                                                        # republishing an existing link is free
    with db.connect() as con:                                                          # last month's links do not count
        con.execute("UPDATE activity SET ts=ts-40*86400 WHERE action='share'")
    assert share(h).status_code == 200


def share_id_of(h):
    return c.get("/api/projects", headers=h).json()[0]["share_id"]


def test_pack_with_preview_pictures_is_validated():
    def pack(az=4, el=1, thumbs="ok"):
        n, off = az * el, 0
        frames = [[i * 5, 5] for i in range(n)]
        th = [[n * 5 + i * 3, 3] for i in range(n)]
        if thumbs == "short": th = th[:-1]
        if thumbs == "outside": th[0] = [10_000, 3]
        ix = json.dumps({"v": 1, "mime": "image/webp", "w": 64, "h": 64, "az": az, "el": el, "frames": frames, "thumbs": th, "anno": None}).encode()
        return b"S3IM" + len(ix).to_bytes(4, "little") + ix + b"1" * (n * 5) + b"2" * (n * 3)
    h = pro("thumb@x.com")
    assert share_images(h, pack=pack()).status_code == 200                           # previews are accepted
    assert share_images(h, pack=mkpack(thumbs=False)).status_code == 200              # older packs without previews still are
    assert share_images(h, pack=pack(thumbs="short")).status_code == 400
    assert share_images(h, pack=pack(thumbs="outside")).status_code == 400


# ---------- watermark baked into the pictures, caching, shared limits, saved projects ----------
def frames_of(pack: bytes):
    n = int.from_bytes(pack[4:8], "little"); ix = json.loads(pack[8:8 + n]); data = pack[8 + n:]
    return ix, [Image.open(io.BytesIO(data[o:o + ln])).convert("RGBA") for o, ln in ix["frames"]], [Image.open(io.BytesIO(data[o:o + ln])).convert("RGBA") for o, ln in ix["thumbs"]]


def test_free_watermark_is_baked_into_the_pictures():
    sid = share(login("bake@x.com")).json()["share_id"]
    ix, frames, thumbs = frames_of(c.get(f"/api/projects/{sid}/images").content)       # what any visitor can download
    flat = Image.open(io.BytesIO(_pic(64, "WEBP"))).convert("RGBA")                     # the picture as the browser sent it
    assert any(f.tobytes() != flat.tobytes() for f in frames)                          # text or cross is in the pixels, no clean copy exists
    assert all(len(set(f.getdata())) > 3 for f in frames) and len(frames) == 4 and len(thumbs) == 4
    ok = frames_of(c.get(f"/api/projects/{share(pro('nobake@x.com')).json()['share_id']}/images").content)[1]
    assert ok[0].tobytes() == flat.tobytes()                                           # Pro: pictures are left exactly as they were


def test_enterprise_custom_watermark_is_baked_and_jpeg_packs_work():
    db.create_license("W W", "wm@x.com", "ENTERPRISE", "30d")
    h = login("wm@x.com")
    sid = share_images(h, pack=mkpack(az=4, el=1, px=96, mime="image/jpeg"), watermark="(c) Mine").json()["share_id"]
    ix, frames, _ = frames_of(c.get(f"/api/projects/{sid}/images").content)
    assert ix["mime"] == "image/jpeg" and len(set(frames[0].getdata())) > 3


def test_big_files_are_cached_but_still_revalidated():
    h = pro("cache@x.com")
    sid = share_images(h, allow_download="true").json()["share_id"]
    for part in ("images", "model"):
        r = c.get(f"/api/projects/{sid}/{part}")
        assert r.status_code == 200 and r.headers["cache-control"] == "private, no-cache" and r.headers["etag"]
        n = c.get(f"/api/projects/{sid}/{part}", headers={"If-None-Match": r.headers["etag"]})
        assert n.status_code == 304 and n.content == b""                               # unchanged: nothing re-sent, Drive not read
    etag = c.get(f"/api/projects/{sid}/images").headers["etag"]
    assert c.patch(f"/api/projects/{sid}", headers=h, json={"revoked": True}).status_code == 200
    assert c.get(f"/api/projects/{sid}/images", headers={"If-None-Match": etag}).status_code == 410   # a revoked link stops at once
    assert c.get(f"/api/projects/{sid}/config").headers["cache-control"] == "no-store"   # views are still counted
    sid2 = share_images(h).json()["share_id"]
    assert c.patch(f"/api/projects/{sid2}", headers=h, json={"revoked": False}).status_code == 200
    assert share_images(h, update_id=sid2).status_code == 200
    assert c.get(f"/api/projects/{sid2}/images").headers["etag"] != etag               # a republished file gets a new validator


def test_rate_limit_counters_live_in_the_database():
    for _ in range(10):
        c.get("/api/me")
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM rate_hits").fetchone()[0] >= 0
    r = None
    for _ in range(125):
        r = c.get("/api/projects/abcdefghij/config")
    assert r.status_code == 429                                                        # the limit counts across requests (and workers)
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM rate_hits WHERE bucket='view'").fetchone()[0] >= 120


def test_signin_state_is_stored_in_the_database(monkeypatch):
    with db.connect() as con:
        con.execute("INSERT INTO oauth_pending VALUES('st1','login',NULL,'v',?)", (time.time() + 600,))
    assert storage.purpose("st1") == "login" and storage.purpose("nope") is None
    with pytest.raises(ValueError):
        storage.finish("expired-or-unknown", "code")
    with db.connect() as con:
        con.execute("UPDATE oauth_pending SET expires=1 WHERE state='st1'")
    with pytest.raises(ValueError):
        storage.finish("st1", "code")                                                  # expired
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM oauth_pending WHERE state='st1'").fetchone()[0] == 0   # one-time: removed when used


def test_saved_projects_roundtrip_and_limits(monkeypatch):
    assert [db.TIERS[t]["max_projects"] for t in ("FREE", "PRO", "ENTERPRISE")] == [10, 100, 1000]
    monkeypatch.setitem(db.TIERS["FREE"], "max_projects", 2)
    h = login("save@x.com")
    save = lambda **d: c.post("/api/drive-projects", headers=h, files={"model": ("m.glb", GLB)}, data={"config": json.dumps({"annotations": []}), "name": "Bracket", **d})
    r = save(thumb="data:image/jpeg;base64,AAAA"); assert r.status_code == 200
    pid = r.json()["id"]
    assert gzip.decompress(next(v for k, v in cloud.items() if k.endswith(f"{pid}.glb.gz".join(["", ""])) or ".glb.gz" in k)) == GLB   # stored in the user's Drive
    listing = c.get("/api/drive-projects", headers=h).json()
    assert listing["total"] == 1 and listing["max"] == 2 and listing["items"][0]["name"] == "Bracket" and listing["items"][0]["thumb"].startswith("data:image/")
    assert c.get(f"/api/drive-projects/{pid}", headers=h).json()["config"] == {"annotations": []}
    assert c.get(f"/api/drive-projects/{pid}/model", headers=h).content == GLB          # opened back from Drive
    old = set(cloud)
    assert save(project_id=pid, name="Bracket v2").status_code == 200                   # save over: same project, old file removed
    assert c.get("/api/drive-projects", headers=h).json()["total"] == 1 and not (old & set(cloud))
    assert c.patch(f"/api/drive-projects/{pid}", headers=h, json={"name": "Renamed"}).status_code == 200
    assert c.get("/api/drive-projects", headers=h).json()["items"][0]["name"] == "Renamed"
    assert save().status_code == 200
    r = save(); assert r.status_code == 403 and "limit" in r.json()["detail"].lower()    # Free keeps 2 here (10 in real life)
    other = login("other-save@x.com")
    assert c.get(f"/api/drive-projects/{pid}", headers=other).status_code == 404         # nobody else can open or delete it
    assert c.delete(f"/api/drive-projects/{pid}", headers=other).status_code == 404
    assert c.post("/api/drive-projects", headers=other, files={"model": ("m.glb", GLB)}, data={"project_id": pid}).status_code == 404
    assert c.delete(f"/api/drive-projects/{pid}", headers=h).status_code == 200
    assert c.get("/api/drive-projects", headers=h).json()["total"] == 1 and len(cloud) == 1   # its Drive file is gone
    assert c.post("/api/drive-projects", headers=h, files={"model": ("m.glb", b"nope")}).status_code == 400
    assert c.post("/api/drive-projects", headers=h, files={"model": ("m.glb", GLB)}, data={"thumb": "javascript:1"}).status_code == 400
    assert c.get("/api/me", headers=h).json()["projects_count"] == 1
    assert not list(db.SHARES.glob("*"))                                                # nothing left on this server


# ---------- publishing and saving run as background jobs with a readable log ----------
def test_publishing_is_a_job_with_a_progress_log():
    h = login("joblog@x.com")                                            # Free: watermark is applied, so that step shows up too
    r = share(h)
    assert r.status_code == 200 and set(r.json()) == {"job", "share_id", "url"}      # answered at once, no long request for a proxy to cut (524)
    j = job_of(h, r)
    text = "\n".join(line[2] for line in j["log"])
    assert j["done"] and not j["error"] and j["percent"] == 100 and j["result"]["share_id"] == r.json()["share_id"]
    assert "Received" in text and "Watermark applied to all pictures." in text
    assert "Uploaded the pictures" in text and text.rstrip().endswith("Done. Your link is ready.")
    assert [x[0] for x in j["log"]] == sorted(x[0] for x in j["log"])                  # times only go forward
    assert c.get(f"/api/projects/{r.json()['share_id']}/config").status_code == 200   # the link is live once the job is done
    assert c.get(f"/api/jobs/{r.json()['job']}", headers=login("snoop@x.com")).status_code == 404   # other people cannot read it
    assert c.get(f"/api/jobs/{r.json()['job']}").status_code == 401
    assert c.get("/api/jobs/abcdefghij", headers=h).status_code == 404
    pro_h = pro("joblog2@x.com")
    text = "\n".join(line[2] for line in job_of(pro_h, share(pro_h))["log"])
    assert "Watermark" not in text and "Uploaded the pictures" in text                 # Pro: no watermark step


def test_job_reports_model_steps_and_unreadable_pictures():
    h = pro("joblog3@x.com")
    text = "\n".join(line[2] for line in job_of(h, share(h, allow_download=True))["log"])
    assert "Compressing the model" in text and "Uploaded the model" in text and "Uploaded the pictures" in text
    cloud.clear()
    garbage = share(login("joblog4@x.com"), pack=mkpack(thumbs=False).replace(_pic(64, "WEBP"), b"x" * len(_pic(64, "WEBP"))))   # right layout, not pictures
    j = job_of(login("joblog4@x.com"), garbage)
    assert j["done"] and j["error"] == "The pictures could not be read."
    assert not list(db.SHARES.glob("*")) and not cloud                                   # nothing left on the server or in Drive


def test_saving_a_project_is_a_job_too():
    h = login("jobsave@x.com")
    r = c.post("/api/drive-projects", headers=h, files={"model": ("m.glb", GLB)}, data={"config": "{}", "name": "P"})
    assert r.status_code == 200 and set(r.json()) >= {"job", "id", "name"}
    j = job_of(h, r)
    text = "\n".join(line[2] for line in j["log"])
    assert j["done"] and not j["error"] and j["result"]["id"] == r.json()["id"] and "Uploading" in text and text.rstrip().endswith("saved in your Google Drive.")
    assert c.get("/api/drive-projects", headers=h).json()["total"] == 1


def test_measuring_is_gone_from_tiers_and_api():
    assert not any("measure" in t["tools"] for t in db.TIERS.values()) and not hasattr(db, "SHARE_FLAG_TOOL")
    h = pro("nomeasure@x.com")
    sid = share(h).json()["share_id"]
    for body in (c.get(f"/api/projects/{sid}/config").json(), c.get(f"/api/projects/{sid}/edit", headers=h).json()):
        assert "allow_measure" not in body and "allow_explode" not in body
    assert "allow_measure" not in c.get("/api/projects", headers=h).json()[0]
    assert "measure" not in c.get("/api/me", headers=h).json()["tools"]
