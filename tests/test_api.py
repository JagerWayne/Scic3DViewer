import gzip, hashlib, hmac, itertools, json, os, tempfile, time
os.environ["SCIC_DATA"] = tempfile.mkdtemp()
import pytest
from fastapi.testclient import TestClient
import app as appmod, billing, db, mailer, storage

c = TestClient(appmod.app)
real_upload = storage.upload  # the fixture below replaces storage.upload with a fake
cloud = {}
ids = itertools.count(1)
GLB = b"glTF" + b"\0" * 100


@pytest.fixture(autouse=True)
def fake_drive(monkeypatch):  # in-memory per-user "Drive"; every test user counts as connected
    cloud.clear()
    monkeypatch.setattr(storage, "configured", lambda: True)
    monkeypatch.setattr(storage, "connected", lambda lid: True)
    def upload(lid, p, name):  # unique ids, like the real Drive
        fid = f"{lid}:{name}:{next(ids)}"; cloud[fid] = p.read_bytes(); return fid
    monkeypatch.setattr(storage, "upload", upload)
    monkeypatch.setattr(storage, "stream", lambda lid, fid: iter([cloud[fid]]))
    monkeypatch.setattr(storage, "delete", lambda lid, fid: cloud.pop(fid, None))


def login(email):  # what the Google callback does once Google has verified the email
    return {"Authorization": "Bearer " + db.login_google("Ann", email, "test")[0]}


def share(h, **flags):
    return c.post("/api/projects/share", headers=h, files={"model": ("m.glb", GLB)},
                  data={"config": "{}", **{k: "true" for k in flags}})


def tier(h):
    return c.get("/api/me", headers=h).json()["tier"]


def test_free_flow_and_gating():
    h = login("free@x.com")
    assert tier(h) == "FREE"
    assert share(h, allow_download=True).status_code == 403   # custom perms need PRO
    sid = share(h).json()["share_id"]
    assert c.get(f"/api/projects/{sid}/model").content == GLB
    assert c.get(f"/api/projects/{sid}/config").json()["allow_download"] is False


def test_license_follows_the_google_email():
    db.create_license("Bob", "B", "pro@x.com", "PRO", "30d")
    assert tier(login("other@x.com")) == "FREE"                    # someone else's email gets nothing
    h = login("PRO@x.com")                                         # case-insensitive match, clock starts now
    me = c.get("/api/me", headers=h).json()
    assert me["tier"] == "PRO" and me["expires_at"]
    assert share(h, allow_download=True).status_code == 200
    assert share(h, allow_measure=True).status_code == 403         # measure is ENTERPRISE only
    with db.connect() as con:
        con.execute("UPDATE licenses SET expires_at=1 WHERE email='pro@x.com'")
    assert tier(h) == "FREE" and tier(login("pro@x.com")) == "FREE"  # expired -> falls back, also on next sign-in
    assert len({db.login_google("A", "free2@x.com", "t")[1]["id"] for _ in range(3)}) == 1  # one free license per email


def test_auth_and_validation():
    assert c.post("/api/projects/share", files={"model": ("m.glb", GLB)}).status_code == 401
    assert c.post("/api/register", json={}).status_code in (404, 405)   # old form login is gone
    h = login("v@x.com")
    assert c.post("/api/projects/share", headers=h, files={"model": ("m.glb", b"nope")}).status_code == 400
    assert "nonce-" in c.get("/studio").headers["content-security-policy"]


def test_drive_only_storage(monkeypatch):
    h = login("drive@x.com")
    sid = share(h).json()["share_id"]
    assert not list(db.SHARES.glob("*")) and cloud                     # nothing stays on local disk
    assert c.get(f"/api/projects/{sid}/model").content == GLB          # streamed back through the server
    assert c.delete(f"/api/projects/{sid}", headers=h).status_code == 200 and not cloud
    monkeypatch.setattr(storage, "upload", lambda *a: 1 / 0)           # Drive failing -> error, nothing left behind
    assert share(h).status_code == 502 and not list(db.SHARES.glob("*"))
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
    db.create_license("M", "V", "mv@x.com", "PRO", "30d")
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
        def execute(self): self.m.getbytes(0, 4); return {"id": "x"}

    class Svc:
        def files(self): return Files()

    monkeypatch.setattr(storage, "_service", lambda lid: (Svc(), "folder"))
    assert real_upload(1, p, "a.glb") == "x"
    p.unlink()


def pro(email):
    db.create_license("P", "P", email, "PRO", "30d")
    return login(email)


def test_link_password_expiry_revoke():
    h = pro("pro2@x.com")
    r = c.post("/api/projects/share", headers=h, files={"model": ("m.glb", GLB)},
               data={"config": "{}", "password": "secret1", "expires_in_days": "7"})
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
        r = c.post("/api/projects/share", headers=h, files={"model": ("m.glb", GLB)}, data={"config": "{}", **extra})
        assert r.status_code == 403
    sid = share(h).json()["share_id"]
    assert c.patch(f"/api/projects/{sid}", headers=h, json={"password": "secret1"}).status_code == 403


def test_projects_list_update_in_place_and_gzip():
    h = pro("pro3@x.com")
    sid = share(h).json()["share_id"]
    assert all(k.split(":")[1].endswith(".glb.gz") for k in cloud)                  # stored compressed in Drive
    assert gzip.decompress(next(iter(cloud.values()))) == GLB
    assert [p["share_id"] for p in c.get("/api/projects", headers=h).json()] == [sid]
    assert c.get("/api/projects", headers=login("nobody@x.com")).json() == []
    old = set(cloud)
    r = c.post("/api/projects/share", headers=h, files={"model": ("m.glb", GLB)}, data={"config": "{}", "update_id": sid})
    assert r.json()["share_id"] == sid and len(cloud) == 1 and not (old & set(cloud))  # same address, old file removed
    assert c.post("/api/projects/share", headers=login("evil@x.com"), files={"model": ("m.glb", GLB)},
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
    new = {"name": "Zed", "surname": "Z", "email": "zed@x.com", "tier": "PRO", "duration": "30d"}
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
    c.post("/api/admin/licenses", headers=adm, json={"name": "Ed", "surname": "It", "email": "ed@x.com", "tier": "PRO", "duration": "30d"})
    rows = lambda: c.get("/api/admin/licenses", headers=adm).json()
    lic = lambda email, paid=True: next(x for x in rows() if x["email"] == email and bool(x["surname"]) == paid)
    lid = lic("ed@x.com")["id"]
    patch = lambda lid_=None, **b: c.patch(f"/api/admin/licenses/{lid_ or lid}", headers=adm, json=b)
    assert patch(name="Edward", surname="Itt", note="vip", duration_days=90, tier="ENTERPRISE").status_code == 200
    l = lic("ed@x.com")
    assert (l["name"], l["surname"], l["note"], l["duration_days"], l["tier"], l["status"]) == ("Edward", "Itt", "vip", 90, "ENTERPRISE", "pending")
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


def test_admin_deletes_a_license_or_a_whole_person():
    adm = login(db.ADMIN_EMAIL)
    rows = lambda: c.get("/api/admin/licenses", headers=adm).json()
    for email in ("keep@x.com", "gone@x.com"):
        c.post("/api/admin/licenses", headers=adm, json={"name": "N", "surname": "S", "email": email, "tier": "PRO", "duration": "30d"})
    paid = lambda email: next(x["id"] for x in rows() if x["email"] == email and x["surname"])
    # delete only the license: the person keeps the account, the link and the Drive file
    keep = login("keep@x.com"); sid = share(keep).json()["share_id"]
    assert c.delete(f"/api/admin/licenses/{paid('keep@x.com')}", headers=adm).status_code == 200
    assert not any(x["email"] == "keep@x.com" and x["surname"] for x in rows())
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
