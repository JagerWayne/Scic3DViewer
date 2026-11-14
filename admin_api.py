"""Admin REST API behind the /admin page. Every route requires a session whose Google-verified email is db.ADMIN_EMAIL."""
import time
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

import billing, db, mailer, storage

EMAIL_RE = r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$"


class NewLicense(BaseModel):
    name: str = Field(min_length=1, max_length=160)   # "Name & Surname"
    email: str = Field(pattern=EMAIL_RE, max_length=320)
    tier: str
    duration: str                    # 30d / 6m / 1y, counted from the customer's first sign-in
    send_email: bool = False


class EditLicense(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=160)   # "Name & Surname"
    email: str | None = Field(None, pattern=EMAIL_RE, max_length=320)
    note: str | None = Field(None, max_length=500)                 # admin-only memo, "" clears it
    tier: str | None = None
    revoked: bool | None = None                                    # permanent block
    paused: bool | None = None                                     # temporary: Free access, the clock is frozen
    expires_on: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")   # activated licenses
    duration_days: int | None = Field(None, ge=1, le=36500)        # licenses waiting for the first sign-in
    extend_days: int | None = Field(None, ge=1, le=3650)
    start_now: bool | None = None                                  # start the clock now instead of at the first sign-in


class EditShare(BaseModel):
    revoked: bool


def status(r) -> str:
    if r["duration_days"] is None:  # an automatic account without a license period
        return "free"
    if r["revoked"]:
        return "revoked"
    if r["paused_at"]:
        return "paused"
    if not r["activated_at"]:
        return "pending"
    return "expired" if r["expires_at"] < time.time() else "active"


def _mail_license(row_email: str, name: str, tier: str, duration: str):
    mailer.send(row_email, "Your Scic3DViewer license",
                f"Hello {name},\n\nYour {tier} license ({duration} from your first sign-in) is ready.\n"
                f"Sign in with the Google account {row_email} at {storage.BASE_URL}/studio and it unlocks automatically.\n")


def register(app, current_user, rate_limit, remove_share):
    def admin(u=Depends(current_user)):
        if not u["is_admin"]:
            raise HTTPException(403, "Admin only")
        return u

    @app.get("/api/admin/stats")
    def stats(u=Depends(admin)):
        now, day = int(time.time()), 86400
        with db.connect() as c:
            one = lambda q, *a: c.execute(q, a).fetchone()[0]
            daily = {}
            for r in c.execute("SELECT date(ts,'unixepoch') d, action, COUNT(*) n FROM activity WHERE ts>? AND action IN "
                               "('login','share','update','view') GROUP BY d, action", (now - 14 * day,)):
                daily.setdefault(r["d"], {})[r["action"]] = r["n"]
            return {
                "people": one("SELECT COUNT(DISTINCT email) FROM licenses"),
                "paid_active": one("SELECT COUNT(*) FROM licenses WHERE duration_days IS NOT NULL AND revoked=0 AND paused_at IS NULL AND activated_at IS NOT NULL AND expires_at>?", now),
                "pending": one("SELECT COUNT(*) FROM licenses WHERE duration_days IS NOT NULL AND revoked=0 AND activated_at IS NULL"),
                "expiring_30d": one("SELECT COUNT(*) FROM licenses WHERE duration_days IS NOT NULL AND revoked=0 AND expires_at BETWEEN ? AND ?", now, now + 30 * day),
                "shares": one("SELECT COUNT(*) FROM shares"), "views": one("SELECT COALESCE(SUM(views),0) FROM shares"),
                "drives": one("SELECT COUNT(*) FROM drive_tokens"),
                "logins_24h": one("SELECT COUNT(*) FROM activity WHERE action='login' AND ts>?", now - day),
                "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
                "config": {"google": storage.configured(), "email": mailer.configured(), "stripe": billing.configured(),
                           "base_url": storage.BASE_URL, "admin": db.ADMIN_EMAIL},
            }

    @app.get("/api/admin/licenses")
    def licenses(u=Depends(admin)):
        with db.connect() as c:
            rows = c.execute("SELECT l.id,l.name,l.email,l.tier,l.duration_days,l.activated_at,l.expires_at,l.revoked,l.paused_at,l.note,"
                             "l.created_at,(SELECT COUNT(*) FROM drive_tokens d WHERE d.license_id=l.id) drive,"
                             "(SELECT COUNT(*) FROM shares s WHERE s.license_id=l.id) shares FROM licenses l ORDER BY l.id DESC").fetchall()
        return [{**dict(r), "status": status(r), "managed": r["duration_days"] is not None} for r in rows]

    @app.post("/api/admin/licenses")
    def create_license(body: NewLicense, request: Request, u=Depends(admin)):
        rate_limit(request, "admin", 60)
        try:
            key = db.create_license(body.name.strip(), body.email, body.tier, body.duration)
        except ValueError as e:
            raise HTTPException(400, str(e))
        out = {"reference_key": key, "emailed": None}
        if body.send_email:
            try:
                _mail_license(body.email, body.name, body.tier.upper(), body.duration); out["emailed"] = True
            except Exception as e:
                out["emailed"] = False; out["email_error"] = str(e)[:200]
        db.log(u["license_id"], "admin:license", request.client.host, f"{body.email} {body.tier} {body.duration}")
        return out

    @app.patch("/api/admin/licenses/{lid}")
    def edit_license(lid: int, body: EditLicense, request: Request, u=Depends(admin)):
        now, ch = int(time.time()), body.model_dump(exclude_none=True)
        with db.connect() as c:
            r = c.execute("SELECT * FROM licenses WHERE id=?", (lid,)).fetchone()
            if not r:
                raise HTTPException(404, "License not found")
            if r["duration_days"] is None:  # an automatic Free account: edit it, or turn it into a license by choosing a tier and a length
                if set(ch) - {"name", "note", "tier", "duration_days", "start_now"}:
                    raise HTTPException(400, "A free account only has a name, a note and a tier")
                if ch.get("tier", "FREE").upper() != "FREE":
                    if "duration_days" not in ch:
                        raise HTTPException(400, "Choose how long the license lasts")
                    ch["start_now"] = True  # the person already has an account, so the license applies right away
                else:
                    ch.pop("duration_days", None); ch.pop("start_now", None)
            sets = {}  # column names below are fixed strings, values are bound parameters
            if "name" in ch:
                sets["name"] = ch["name"].strip()
            if "email" in ch:
                sets["email"] = ch["email"].strip().lower()
            if "note" in ch:
                sets["note"] = ch["note"].strip() or None
            if "revoked" in ch:
                sets["revoked"] = int(ch["revoked"])
            if "tier" in ch:
                if ch["tier"].upper() not in db.TIERS:
                    raise HTTPException(400, "Unknown tier")
                sets["tier"] = ch["tier"].upper()
            if "expires_on" in ch:
                if not r["activated_at"]:
                    raise HTTPException(400, "Not activated yet: change the duration instead")
                try:
                    day = datetime.strptime(ch["expires_on"], "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(400, "Date must look like 2027-01-31")
                sets["expires_at"] = int((day + timedelta(days=1)).timestamp()) - 1  # the end of that day, server time
            if "duration_days" in ch:
                if r["activated_at"]:
                    raise HTTPException(400, "Already activated: change the expiry date instead")
                sets["duration_days"] = ch["duration_days"]
            if ch.get("start_now"):
                if r["activated_at"]:
                    raise HTTPException(400, "The license has already started")
                sets["activated_at"] = now
                sets["expires_at"] = now + sets.get("duration_days", r["duration_days"]) * 86400
            if "extend_days" in ch:
                if r["activated_at"]:
                    sets["expires_at"] = sets.get("expires_at", r["expires_at"]) + ch["extend_days"] * 86400
                else:  # the clock has not started yet, so lengthen the duration
                    sets["duration_days"] = sets.get("duration_days", r["duration_days"]) + ch["extend_days"]
            if "paused" in ch:
                if ch["paused"] and not r["paused_at"]:
                    sets["paused_at"] = now
                elif not ch["paused"] and r["paused_at"]:
                    sets["paused_at"] = None
                    if r["activated_at"] and "expires_at" not in sets:  # a pause does not cost the customer any time
                        sets["expires_at"] = r["expires_at"] + (now - r["paused_at"])
            if sets:
                c.execute(f"UPDATE licenses SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), lid))
        db.log(u["license_id"], "admin:edit", request.client.host, f"#{lid} {sorted(ch)}")
        return {"ok": True}

    @app.delete("/api/admin/licenses/{lid}")
    def delete_license(lid: int, request: Request, purge: bool = False, u=Depends(admin)):
        with db.connect() as c:
            r = c.execute("SELECT * FROM licenses WHERE id=?", (lid,)).fetchone()
        if not r:
            raise HTTPException(404, "License not found")
        if r["email"] == db.ADMIN_EMAIL:
            raise HTTPException(400, "The admin account cannot be deleted")
        if not purge:  # only the paid license goes; the person keeps their account and links (back to Free)
            if r["duration_days"] is None:
                raise HTTPException(400, "A free account holds the person's data: use Delete person")
            db.delete_license(lid)
        else:  # everything about this email: links (and their Drive files), Drive login, sessions, log, licenses
            ids = db.license_ids(r["email"]); q = ",".join("?" * len(ids))
            with db.connect() as c:
                shares = c.execute(f"SELECT * FROM shares WHERE license_id IN ({q})", ids).fetchall()
            for s in shares:
                remove_share(s)
            with db.connect() as c:
                projects = c.execute(f"SELECT * FROM drive_projects WHERE license_id IN ({q})", ids).fetchall()
                c.execute(f"DELETE FROM drive_projects WHERE license_id IN ({q})", ids)
            for pr in projects:
                storage.delete(pr["license_id"], pr["drive_id"])
            for i in ids:
                storage.disconnect(i)
            with db.connect() as c:
                for table in ("sessions", "activity"):
                    c.execute(f"DELETE FROM {table} WHERE license_id IN ({q})", ids)
                c.execute(f"DELETE FROM licenses WHERE id IN ({q})", ids)
        db.log(u["license_id"], "admin:delete_person" if purge else "admin:delete_license", request.client.host, r["email"])
        return {"ok": True}

    @app.post("/api/admin/licenses/{lid}/email")
    def resend(lid: int, u=Depends(admin)):
        with db.connect() as c:
            r = c.execute("SELECT * FROM licenses WHERE id=? AND duration_days IS NOT NULL", (lid,)).fetchone()
        if not r:
            raise HTTPException(404)
        try:
            _mail_license(r["email"], r["name"], r["tier"], f"{r['duration_days']}d")
        except Exception as e:
            raise HTTPException(502, str(e)[:200])
        return {"ok": True}

    @app.get("/api/admin/shares")
    def shares(u=Depends(admin)):
        with db.connect() as c:
            rows = c.execute("SELECT s.share_id,s.title,s.created_at,s.views,s.expires_at,s.revoked,s.pw_hash IS NOT NULL locked,"
                             "l.email FROM shares s LEFT JOIN licenses l ON l.id=s.license_id ORDER BY s.created_at DESC").fetchall()
        return [dict(r) for r in rows]

    @app.patch("/api/admin/shares/{share_id}")
    def edit_share(share_id: str, body: EditShare, u=Depends(admin)):
        with db.connect() as c:
            if not c.execute("UPDATE shares SET revoked=? WHERE share_id=?", (int(body.revoked), share_id)).rowcount:
                raise HTTPException(404)
        return {"ok": True}

    @app.delete("/api/admin/shares/{share_id}")
    def delete_share(share_id: str, request: Request, u=Depends(admin)):
        with db.connect() as c:
            r = c.execute("SELECT * FROM shares WHERE share_id=?", (share_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        remove_share(r)
        db.log(u["license_id"], "admin:delete_share", request.client.host, share_id)
        return {"ok": True}

    @app.get("/api/admin/activity")
    def activity(q: str = "", limit: int = 200, u=Depends(admin)):
        like = f"%{q.strip()}%"
        with db.connect() as c:
            rows = c.execute("SELECT a.ts,a.action,a.ip,a.detail,l.email FROM activity a LEFT JOIN licenses l ON l.id=a.license_id "
                             "WHERE ?='%%' OR a.action LIKE ? OR l.email LIKE ? OR a.detail LIKE ? ORDER BY a.id DESC LIMIT ?",
                             (like, like, like, like, max(1, min(limit, 500)))).fetchall()
        return [dict(r) for r in rows]
