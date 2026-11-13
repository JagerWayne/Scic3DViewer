"""Admin REST API behind the /admin page. Every route requires a session whose Google-verified email is db.ADMIN_EMAIL."""
import time

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

import billing, db, mailer, storage

EMAIL_RE = r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$"


class NewLicense(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    surname: str = Field(min_length=1, max_length=80)
    email: str = Field(pattern=EMAIL_RE, max_length=320)
    tier: str
    duration: str                    # 30d / 6m / 1y, counted from the customer's first sign-in
    send_email: bool = False


class EditLicense(BaseModel):
    revoked: bool | None = None
    tier: str | None = None
    extend_days: int | None = Field(None, ge=1, le=3650)


class EditShare(BaseModel):
    revoked: bool


def status(r) -> str:
    if r["surname"] is None:
        return "free"
    if r["revoked"]:
        return "revoked"
    if not r["activated_at"]:
        return "pending"
    return "expired" if r["expires_at"] < time.time() else "active"


def _mail_license(row_email: str, name: str, tier: str, duration: str):
    mailer.send(row_email, "Your Scic3D license",
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
                "paid_active": one("SELECT COUNT(*) FROM licenses WHERE surname IS NOT NULL AND revoked=0 AND activated_at IS NOT NULL AND expires_at>?", now),
                "pending": one("SELECT COUNT(*) FROM licenses WHERE surname IS NOT NULL AND revoked=0 AND activated_at IS NULL"),
                "expiring_30d": one("SELECT COUNT(*) FROM licenses WHERE surname IS NOT NULL AND revoked=0 AND expires_at BETWEEN ? AND ?", now, now + 30 * day),
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
            rows = c.execute("SELECT l.id,l.name,l.surname,l.email,l.tier,l.duration_days,l.activated_at,l.expires_at,l.revoked,"
                             "l.created_at,(SELECT COUNT(*) FROM drive_tokens d WHERE d.license_id=l.id) drive,"
                             "(SELECT COUNT(*) FROM shares s WHERE s.license_id=l.id) shares FROM licenses l ORDER BY l.id DESC").fetchall()
        return [{**dict(r), "status": status(r)} for r in rows]

    @app.post("/api/admin/licenses")
    def create_license(body: NewLicense, request: Request, u=Depends(admin)):
        rate_limit(request, "admin", 60)
        try:
            key = db.create_license(body.name.strip(), body.surname.strip(), body.email, body.tier, body.duration)
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
        with db.connect() as c:
            r = c.execute("SELECT * FROM licenses WHERE id=? AND surname IS NOT NULL", (lid,)).fetchone()
            if not r:
                raise HTTPException(404, "License not found (free accounts are managed automatically)")
            if body.revoked is not None:
                c.execute("UPDATE licenses SET revoked=? WHERE id=?", (int(body.revoked), lid))
            if body.tier is not None:
                if body.tier.upper() not in db.TIERS:
                    raise HTTPException(400, "Unknown tier")
                c.execute("UPDATE licenses SET tier=? WHERE id=?", (body.tier.upper(), lid))
            if body.extend_days:
                if r["activated_at"]:
                    c.execute("UPDATE licenses SET expires_at=expires_at+? WHERE id=?", (body.extend_days * 86400, lid))
                else:  # not yet activated: the clock has not started, lengthen the duration instead
                    c.execute("UPDATE licenses SET duration_days=duration_days+? WHERE id=?", (body.extend_days, lid))
        db.log(u["license_id"], "admin:edit", request.client.host, f"#{lid} {body.model_dump(exclude_none=True)}")
        return {"ok": True}

    @app.post("/api/admin/licenses/{lid}/email")
    def resend(lid: int, u=Depends(admin)):
        with db.connect() as c:
            r = c.execute("SELECT * FROM licenses WHERE id=? AND surname IS NOT NULL", (lid,)).fetchone()
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
