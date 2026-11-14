import gzip, hashlib, itertools, json, os, re, secrets, shutil, time, zlib
from html import escape
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import admin_api
import billing
import db
import storage
import watermark as wmark

db.init()
WEB = Path(__file__).parent / "web"
MAX_BODY = 520 * db.MB
OPERATOR = os.environ.get("SCIC_OPERATOR", "Scic3DViewer")  # shown in the legal pages
CONTACT = os.environ.get("SCIC_CONTACT_EMAIL", db.ADMIN_EMAIL)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,24}$")
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


# ---------- security ----------
CSP = ("default-src 'none'; script-src 'self' 'nonce-{n}' 'wasm-unsafe-eval' https://cdn.jsdelivr.net https://cdn.tailwindcss.com; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src blob:; "
       "connect-src 'self' blob: data: https://www.gstatic.com https://cdn.jsdelivr.net; worker-src blob:; "
       "frame-ancestors {fa}; base-uri 'none'; form-action 'self'")


def rate_limit(request: Request, bucket: str, limit: int, per: int = 60):
    """Counters live in SQLite (not in memory), so several server workers share one limit."""
    now, ip = time.time(), request.client.host
    with db.connect() as c:
        c.execute("DELETE FROM rate_hits WHERE bucket=? AND ip=? AND ts<?", (bucket, ip, now - per))
        if c.execute("SELECT COUNT(*) FROM rate_hits WHERE bucket=? AND ip=?", (bucket, ip)).fetchone()[0] >= limit:
            raise HTTPException(429, "Too many requests, slow down")
        c.execute("INSERT INTO rate_hits VALUES(?,?,?)", (bucket, ip, now))


@app.middleware("http")
async def guard(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse({"detail": "Payload too large"}, 413)
    r = await call_next(request)
    r.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                      "Cache-Control": r.headers.get("Cache-Control", "no-store")})
    if not request.url.path.startswith("/v/"):  # only the viewer may be embedded in other sites
        r.headers["X-Frame-Options"] = "DENY"
    return r


def page(name: str, embed: bool = False):
    nonce = secrets.token_urlsafe(12)
    html = (WEB / name).read_text("utf-8").replace("{{NONCE}}", nonce).replace("{{OPERATOR}}", escape(OPERATOR)).replace("{{CONTACT}}", escape(CONTACT))
    return HTMLResponse(html, headers={"Content-Security-Policy": CSP.format(n=nonce, fa="*" if embed else "'none'")})


def current_user(authorization: str = Header(default="")):
    u = db.session_user(authorization[7:]) if authorization.startswith("Bearer ") else None
    if not u:
        raise HTTPException(401, "Not signed in")
    return u


def me_payload(u):
    return {"name": u["name"], "email": u["email"], "tier": u["tier"], "expires_at": u["expires_at"],
            "is_admin": u["is_admin"], "billing_available": billing.configured(),
            "drive_available": storage.configured(), "drive_connected": storage.connected(u["license_id"]),
            "shares_month": db.shares_this_month(u["email"]), "projects_count": db.projects_count(u["email"]), **db.TIERS[u["tier"]]}


# ---------- pages ----------
@app.get("/", include_in_schema=False)
def home():  # public page that explains the app and links the legal pages (Google verification needs it)
    return page("home.html")


@app.get("/studio")
def studio():
    return page("studio.html")


@app.get("/admin")
def admin_page():  # the page is static; every admin API call checks the signed-in Google email
    return page("admin.html")


@app.get("/privacy")
def privacy_page():
    return page("privacy.html")


@app.get("/terms")
def terms_page():
    return page("terms.html")


@app.get("/help/drive")
def help_drive():
    return page("help-drive.html")


@app.get("/v/{share_id}")
def viewer(share_id: str):
    if not ID_RE.match(share_id):
        raise HTTPException(404)
    return page("viewer.html", embed=True)


# ---------- sign in with Google ----------
@app.get("/auth/google/login", include_in_schema=False)
def google_login(request: Request):
    rate_limit(request, "login", 20)
    if not storage.configured():
        return RedirectResponse("/studio?login=unavailable", 303)
    try:
        return RedirectResponse(storage.begin("login"), 303)
    except ValueError:
        return RedirectResponse("/studio?login=error", 303)


@app.get("/api/me")
def me(u=Depends(current_user)):
    return me_payload(u)


# ---------- each user's own Google Drive ----------
@app.post("/api/drive/connect")
def drive_connect(request: Request, u=Depends(current_user)):
    rate_limit(request, "drive", 10)
    if not storage.configured():
        raise HTTPException(503, "Google Drive is not enabled on this site yet. Ask the site owner.")
    try:
        return {"url": storage.begin("drive", u["license_id"])}
    except ValueError:
        raise HTTPException(503, "Busy, try again in a minute")


@app.get("/auth/google/callback", include_in_schema=False)
def drive_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    rate_limit(request, "drive", 20)
    kind = storage.purpose(state)
    try:
        if error or not code:
            raise ValueError(error or "cancelled")
        kind, lid, claims = storage.finish(state, code)
        if kind == "login":  # token goes in the URL fragment: never sent to a server or written to logs
            drive_token = claims.pop("drive_token", None)
            token, lic = db.login_google(claims.get("name"), claims["email"], request.client.host)
            if drive_token:  # Drive was allowed on the same Google screen
                storage.save_token(lic["id"], drive_token)
                db.log(lic["id"], "drive_connect", request.client.host, "at sign-in")
            return RedirectResponse("/studio#token=" + token, 303)
        db.log(lid, "drive_connect", request.client.host)
        return RedirectResponse("/studio?drive=connected", 303)
    except Exception:
        return RedirectResponse("/studio?" + ("login" if kind == "login" else "drive") + "=error", 303)


@app.delete("/api/drive")
def drive_disconnect(u=Depends(current_user)):
    storage.disconnect(u["license_id"])
    db.log(u["license_id"], "drive_disconnect")
    return {"ok": True}


# ---------- shares ----------
def _gzip(src: Path, dst: Path):  # lossless: Drive stores the .gz, viewers get the original GLB back
    with open(src, "rb") as a, gzip.open(dst, "wb", compresslevel=6) as b:
        shutil.copyfileobj(a, b, db.MB)


async def _save_pack(up: UploadFile, dest: Path, im: dict):
    """Stage a 360 image pack and check it against the owner's tier. Layout: b'S3IM' + u32 index length + JSON index + frames."""
    size = 0
    with open(dest, "wb") as f:
        while chunk := await up.read(db.MB):
            size += len(chunk)
            if size > im["max_mb"] * db.MB:
                raise HTTPException(413, f"Image pack limit of your tier is {im['max_mb']} MB")
            f.write(chunk)
    try:
        with open(dest, "rb") as f:
            head = f.read(8)
            n = int.from_bytes(head[4:8], "little")
            assert head[:4] == b"S3IM" and 0 < n <= db.MB
            ix = json.loads(f.read(n))
        az, el, w, h = (int(ix[k]) for k in ("az", "el", "w", "h"))
        data = size - 8 - n
        assert 1 <= az <= im["max_az"] and 1 <= el <= im["max_el"] and 1 <= w <= im["max_px"] and 1 <= h <= im["max_px"]
        assert ix["mime"] in ("image/webp", "image/jpeg") and len(ix["frames"]) == az * el
        assert all(o >= 0 and ln > 0 and o + ln <= data for o, ln in ix["frames"])
        assert ix.get("anno") is None or len(ix["anno"]) == az * el
        th = ix.get("thumbs")  # small preview pictures, optional (older packs have none)
        assert th is None or (len(th) == az * el and all(o >= 0 and ln > 0 and o + ln <= data for o, ln in th))
    except Exception:
        raise HTTPException(400, "Invalid image pack, or it is above the limits of your tier")


def get_share(share_id: str):
    if not ID_RE.match(share_id):
        raise HTTPException(404)
    with db.connect() as c:
        r = c.execute("SELECT * FROM shares WHERE share_id=?", (share_id,)).fetchone()
    if not r:
        raise HTTPException(404)
    return r


def owned_share(share_id: str, u):
    r = get_share(share_id)
    if r["license_id"] not in db.license_ids(u["email"]):
        raise HTTPException(403, "Not your project")
    return r


def check_access(r, request: Request):
    if r["revoked"] or (r["expires_at"] and r["expires_at"] < time.time()):
        raise HTTPException(410, "This link has expired or was removed by its owner.")
    if r["pw_hash"]:
        pw = request.headers.get("x-share-password", "")
        if pw:
            rate_limit(request, "pw", 10)  # slow down password guessing
        if not pw or not db.check_pw(pw, r["pw_hash"]):
            raise HTTPException(401, "password")


@app.post("/api/projects/share")
async def share(request: Request, model: UploadFile | None = File(None), config: str = Form("{}"),
                title: str = Form("Untitled", max_length=120), allow_download: bool = Form(False),
                update_id: str = Form(""), expires_in_days: int = Form(0, ge=0, le=3650),
                password: str = Form("", max_length=64), kind: str = Form("images"), watermark: str = Form("", max_length=40),
                pack: UploadFile | None = File(None), u=Depends(current_user)):
    rate_limit(request, "share", 20)
    lid = u["license_id"]
    if not storage.connected(lid):
        raise HTTPException(409, "Connect your Google Drive first (Drive button in the top bar).")
    tier = db.TIERS[u["tier"]]
    old = owned_share(update_id, u) if update_id else None
    if not old and db.shares_this_month(u["email"]) >= tier["max_shares_month"]:  # republishing an existing link is free
        raise HTTPException(403, f"Monthly limit reached: the {u['tier']} tier allows {tier['max_shares_month']} new links per month. It resets on the 1st.")
    if len(config) > 256 * 1024:
        raise HTTPException(413, "scene config too large")
    try:
        cfg = json.loads(config)
        assert isinstance(cfg, dict)
    except Exception:
        raise HTTPException(400, "config must be a JSON object")
    n_ann = len(cfg.get("annotations") or [])
    if tier["max_annotations"] is not None and n_ann > tier["max_annotations"]:
        raise HTTPException(403, f"{u['tier']} tier allows {tier['max_annotations']} annotations")
    if kind != "images":
        raise HTTPException(400, "Only 360 degree picture links can be created")
    if (allow_download or expires_in_days or password) and not tier["share_permissions"]:
        raise HTTPException(403, "Model downloads, link expiry and passwords need the PRO tier")
    if password and len(password) < 4:
        raise HTTPException(400, "Password must have at least 4 characters")
    im = tier["images"]
    if pack is None or not pack.filename:
        raise HTTPException(400, "The rendered picture pack is missing")
    has_model = model is not None and bool(model.filename)  # the model is only kept when visitors may download it
    if allow_download and not has_model:
        raise HTTPException(400, "The model file is missing")
    if has_model:
        if await model.read(4) != b"glTF":
            raise HTTPException(400, "Model must be a binary .glb")
        await model.seek(0)
    sid = update_id or secrets.token_urlsafe(9)
    mode, text = im["watermark"], watermark.strip()
    wm = {"text": "Scic3DViewer", "tile": True, "cross": True} if mode == "forced" else \
         {"text": text, "tile": False, "cross": False} if mode == "custom" and text else None  # chosen here, never by the browser
    packfile, dest, size = db.SHARES / f"{sid}.pack", db.SHARES / f"{sid}.glb", 0
    try:
        await _save_pack(pack, packfile, im)
        if wm:  # drawn into every picture: nobody can fetch clean pictures from the link
            try:
                await run_in_threadpool(wmark.bake, packfile, wm["text"], wm["tile"], wm["cross"])
            except Exception:
                raise HTTPException(400, "The pictures could not be read")
            wm["baked"] = True
        if has_model:
            with open(dest, "wb") as f:
                while chunk := await model.read(db.MB):
                    size += len(chunk)
                    if size > tier["max_mb"] * db.MB:
                        raise HTTPException(413, f"{u['tier']} tier limit is {tier['max_mb']} MB")
                    f.write(chunk)
    except BaseException:
        dest.unlink(missing_ok=True)
        packfile.unlink(missing_ok=True)
        raise
    drive = {}  # data/shares is only a short-lived staging area: files move to Drive and the local copy is removed
    try:
        if has_model:
            await run_in_threadpool(_gzip, dest, db.SHARES / f"{sid}.glb.gz")
        for key, fname in (("glbz", f"{sid}.glb.gz"), ("pack", f"{sid}.pack")):
            p = db.SHARES / fname
            if p.exists():
                drive[key] = await run_in_threadpool(storage.upload, lid, p, fname)
    except Exception as e:
        for fid in drive.values():
            storage.delete(lid, fid)
        if isinstance(e, PermissionError):
            raise HTTPException(409, "Connect your Google Drive first (Drive button in the top bar).")
        raise HTTPException(502, "Upload to your Google Drive failed. Check your connection and free space, or reconnect Drive.")
    finally:
        for ext in ("glb", "glb.gz", "pack"):
            (db.SHARES / f"{sid}.{ext}").unlink(missing_ok=True)
    exp = int(time.time() + expires_in_days * 86400) if expires_in_days else None
    pw = db.hash_pw(password) if password else None
    fmts = json.dumps((["glb"] if "glb" in tier["formats"] else []) if allow_download else [])  # only the original GLB can be offered
    vals = (title, json.dumps(cfg), int(allow_download), 0, 0, fmts, json.dumps(drive), exp, pw, kind, json.dumps(wm) if wm else None)
    with db.connect() as c:
        if old:
            c.execute("UPDATE shares SET title=?,config=?,allow_download=?,allow_measure=?,allow_explode=?,formats=?,"
                      "drive_ids=?,expires_at=?,pw_hash=?,kind=?,wm=?,revoked=0 WHERE share_id=?", (*vals, sid))
        else:
            c.execute("INSERT INTO shares(title,config,allow_download,allow_measure,allow_explode,formats,drive_ids,"
                      "expires_at,pw_hash,kind,wm,share_id,license_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (*vals, sid, lid, int(time.time())))
    if old:  # the previous files are only removed once the new ones are safely stored
        for fid in json.loads(old["drive_ids"] or "{}").values():
            storage.delete(old["license_id"], fid)
    db.log(lid, "update" if old else "share", request.client.host, f"{sid} {size}B")
    return {"share_id": sid, "url": f"/v/{sid}"}


class Patch(BaseModel):
    revoked: bool | None = None
    expires_in_days: int | None = Field(None, ge=0, le=3650)   # 0 = never expires
    password: str | None = Field(None, max_length=64)           # "" = remove password


@app.get("/api/projects")
def my_projects(u=Depends(current_user)):
    ids = db.license_ids(u["email"])
    with db.connect() as c:
        rows = c.execute(f"SELECT share_id,title,kind,created_at,views,allow_download,allow_measure,allow_explode,expires_at,"
                         f"revoked,pw_hash IS NOT NULL AS locked,drive_ids FROM shares WHERE license_id IN ({','.join('?' * len(ids))}) "
                         f"ORDER BY created_at DESC", ids).fetchall()
    out = []
    for r in rows:
        d = dict(r); fids = json.loads(d.pop("drive_ids") or "{}")
        d["has_model"] = "glbz" in fids or "glb" in fids  # links published without a model download cannot be reopened for editing
        out.append(d)
    return out


@app.patch("/api/projects/{share_id}")
def edit_project(share_id: str, body: Patch, u=Depends(current_user)):
    owned_share(share_id, u)
    if (body.expires_in_days or body.password) and not db.TIERS[u["tier"]]["share_permissions"]:
        raise HTTPException(403, "Expiry and passwords need the PRO tier")
    if body.password and len(body.password) < 4:
        raise HTTPException(400, "Password must have at least 4 characters")
    with db.connect() as c:
        if body.revoked is not None:
            c.execute("UPDATE shares SET revoked=? WHERE share_id=?", (int(body.revoked), share_id))
        if body.expires_in_days is not None:
            c.execute("UPDATE shares SET expires_at=? WHERE share_id=?",
                      (int(time.time() + body.expires_in_days * 86400) if body.expires_in_days else None, share_id))
        if body.password is not None:
            c.execute("UPDATE shares SET pw_hash=? WHERE share_id=?", (db.hash_pw(body.password) if body.password else None, share_id))
    return {"ok": True}


@app.get("/api/projects/{share_id}/edit")
def project_for_edit(share_id: str, u=Depends(current_user)):  # owner-only: ignores expiry and password
    r = owned_share(share_id, u)
    return {"title": r["title"], "config": json.loads(r["config"]), "allow_download": bool(r["allow_download"]),
            "allow_measure": bool(r["allow_measure"]), "allow_explode": bool(r["allow_explode"])}


@app.get("/api/projects/{share_id}/edit-model")
def project_model_for_edit(share_id: str, u=Depends(current_user)):
    return serve(owned_share(share_id, u), "glb", "model/gltf-binary")


@app.get("/api/projects/{share_id}/config")
def share_config(share_id: str, request: Request):
    rate_limit(request, "view", 120)
    r = get_share(share_id)
    check_access(r, request)
    with db.connect() as c:
        c.execute("UPDATE shares SET views=views+1 WHERE share_id=?", (share_id,))
    db.log(r["license_id"], "view", request.client.host, share_id)
    return {"title": r["title"], "config": json.loads(r["config"]), "formats": json.loads(r["formats"]),
            "allow_download": bool(r["allow_download"]), "allow_measure": bool(r["allow_measure"]),
            "allow_explode": bool(r["allow_explode"]), "kind": r["kind"] or "model",
            "watermark": json.loads(r["wm"]) if r["wm"] else None}


def _gunzip(chunks):
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    for ch in chunks:
        if out := d.decompress(ch):
            yield out
    if out := d.flush():
        yield out


def serve(r, ext: str, mime: str, request: Request | None = None):
    ids = json.loads(r["drive_ids"] or "{}")
    fid = ids.get("glbz") if ext == "glb" else None
    zipped = bool(fid)
    fid = fid or ids.get(ext)  # shares made before compression keep a plain "glb" entry
    if not fid:
        raise HTTPException(404)
    cache = {}
    if request is not None:  # visitors: the browser keeps the file but asks first, so revoked or expired links still stop at once,
        etag = '"' + hashlib.sha1(f"{ext}:{fid}".encode()).hexdigest()[:20] + '"'  # and an unchanged file is never re-sent or re-read from Drive
        cache = {"ETag": etag, "Cache-Control": "private, no-cache"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=cache)
    gen = storage.stream(r["license_id"], fid)  # the owner's own Drive; fetch the first chunk now so failures return a clean error
    if zipped:
        gen = _gunzip(gen)
    try:
        first = next(gen, b"")
    except Exception:
        raise HTTPException(502, "This model is temporarily unavailable")
    return StreamingResponse(itertools.chain([first], gen), media_type=mime, headers=cache)


@app.get("/api/projects/{share_id}/model")
def share_model(share_id: str, request: Request):
    r = get_share(share_id)
    check_access(r, request)
    if not r["allow_download"]:
        raise HTTPException(404)  # the model is only served when the owner allowed downloads
    return serve(r, "glb", "model/gltf-binary", request)


@app.get("/api/projects/{share_id}/images")
def share_images(share_id: str, request: Request):
    r = get_share(share_id)
    check_access(r, request)
    if r["kind"] != "images":
        raise HTTPException(404)
    return serve(r, "pack", "application/octet-stream", request)


def remove_share(r):
    """Delete a share and its files from the owner's Drive (used by the owner and by the admin)."""
    with db.connect() as c:
        c.execute("DELETE FROM shares WHERE share_id=?", (r["share_id"],))
    for fid in json.loads(r["drive_ids"] or "{}").values():
        storage.delete(r["license_id"], fid)


@app.delete("/api/projects/{share_id}")
def delete_share(share_id: str, u=Depends(current_user)):
    remove_share(owned_share(share_id, u))
    return {"ok": True}


# ---------- saved projects (Studio "Save to Google Drive") ----------
class ProjectName(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)


def owned_project(pid: str, u):
    if not ID_RE.match(pid):
        raise HTTPException(404, "Project not found")
    with db.connect() as c:
        r = c.execute("SELECT * FROM drive_projects WHERE id=?", (pid,)).fetchone()
    if not r or r["license_id"] not in db.license_ids(u["email"]):
        raise HTTPException(404, "Project not found")  # 404 for other people's projects too, so ids cannot be probed
    return r


@app.post("/api/drive-projects")
async def save_project(request: Request, model: UploadFile = File(...), config: str = Form("{}"), name: str = Form("Untitled", max_length=80),
                       thumb: str = Form("", max_length=60000), project_id: str = Form(""), u=Depends(current_user)):
    rate_limit(request, "save", 30)
    lid = u["license_id"]
    if not storage.connected(lid):
        raise HTTPException(409, "Connect your Google Drive first (Drive menu).")
    tier = db.TIERS[u["tier"]]
    old = owned_project(project_id, u) if project_id else None
    if not old and db.projects_count(u["email"]) >= tier["max_projects"]:
        raise HTTPException(403, f"Project limit reached: the {u['tier']} tier keeps {tier['max_projects']} saved projects. Delete one first.")
    if len(config) > 256 * 1024:
        raise HTTPException(413, "scene config too large")
    try:
        assert isinstance(json.loads(config), dict)
    except Exception:
        raise HTTPException(400, "config must be a JSON object")
    if thumb and not thumb.startswith("data:image/"):
        raise HTTPException(400, "bad thumbnail")
    if await model.read(4) != b"glTF":
        raise HTTPException(400, "Model must be a binary .glb")
    await model.seek(0)
    pid = old["id"] if old else secrets.token_urlsafe(9)
    dest, zdest, size = db.SHARES / f"p{pid}.glb", db.SHARES / f"p{pid}.glb.gz", 0
    try:
        with open(dest, "wb") as f:
            while chunk := await model.read(db.MB):
                size += len(chunk)
                if size > tier["max_mb"] * db.MB:
                    raise HTTPException(413, f"{u['tier']} tier limit is {tier['max_mb']} MB")
                f.write(chunk)
        await run_in_threadpool(_gzip, dest, zdest)
        fid = await run_in_threadpool(storage.upload, lid, zdest, f"{pid}.glb.gz")
    except HTTPException:
        raise
    except PermissionError:
        raise HTTPException(409, "Connect your Google Drive first (Drive menu).")
    except Exception:
        raise HTTPException(502, "Upload to your Google Drive failed. Check your connection and free space, or reconnect Drive.")
    finally:
        dest.unlink(missing_ok=True)
        zdest.unlink(missing_ok=True)
    now, name = int(time.time()), name.strip() or "Untitled"
    with db.connect() as c:
        if old:
            c.execute("UPDATE drive_projects SET license_id=?,name=?,config=?,drive_id=?,size=?,thumb=?,updated_at=? WHERE id=?",
                      (lid, name, config, fid, size, thumb, now, pid))
        else:
            c.execute("INSERT INTO drive_projects(id,license_id,name,config,drive_id,size,thumb,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (pid, lid, name, config, fid, size, thumb, now, now))
    if old:
        storage.delete(old["license_id"], old["drive_id"])
    db.log(lid, "project_save", request.client.host, f"{pid} {size}B")
    return {"id": pid, "name": name, "updated_at": now}


@app.get("/api/drive-projects")
def list_projects(limit: int = 60, offset: int = 0, u=Depends(current_user)):
    ids = db.license_ids(u["email"]); q = ",".join("?" * len(ids))
    with db.connect() as c:
        rows = c.execute(f"SELECT id,name,size,thumb,created_at,updated_at FROM drive_projects WHERE license_id IN ({q}) "
                         f"ORDER BY updated_at DESC LIMIT ? OFFSET ?", (*ids, max(1, min(limit, 100)), max(0, offset))).fetchall()
    return {"items": [dict(r) for r in rows], "total": db.projects_count(u["email"]), "max": db.TIERS[u["tier"]]["max_projects"]}


@app.get("/api/drive-projects/{pid}")
def open_project(pid: str, u=Depends(current_user)):
    r = owned_project(pid, u)
    return {"id": r["id"], "name": r["name"], "config": json.loads(r["config"] or "{}")}


@app.get("/api/drive-projects/{pid}/model")
def open_project_model(pid: str, u=Depends(current_user)):
    r = owned_project(pid, u)
    return serve({"drive_ids": json.dumps({"glbz": r["drive_id"]}), "license_id": r["license_id"]}, "glb", "model/gltf-binary")


@app.patch("/api/drive-projects/{pid}")
def rename_project(pid: str, body: ProjectName, u=Depends(current_user)):
    owned_project(pid, u)
    with db.connect() as c:
        c.execute("UPDATE drive_projects SET name=? WHERE id=?", (body.name.strip() or "Untitled", pid))
    return {"ok": True}


@app.delete("/api/drive-projects/{pid}")
def delete_project(pid: str, u=Depends(current_user)):
    r = owned_project(pid, u)
    with db.connect() as c:
        c.execute("DELETE FROM drive_projects WHERE id=?", (pid,))
    storage.delete(r["license_id"], r["drive_id"])
    return {"ok": True}


admin_api.register(app, current_user, rate_limit, remove_share)
billing.register(app, current_user, rate_limit)
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
