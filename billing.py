"""Stripe Checkout (one-time payment per license period). A paid, signature-verified webhook creates the license.
Config: STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, SCIC_PRICES='{"PRO:1m":"price_..","PRO:1y":"price_..","ENTERPRISE:1y":"price_.."}'."""
import hashlib, hmac, json, os, time

import requests
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

import db, mailer, storage

KEY = os.environ.get("STRIPE_SECRET_KEY", "")
HOOK = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
try:
    PRICES = json.loads(os.environ.get("SCIC_PRICES", "{}"))
except ValueError:
    PRICES = {}


def configured() -> bool:
    return bool(KEY and HOOK and PRICES)


def verify(payload: bytes, header: str, secret: str = "", tolerance: int = 300) -> bool:
    """Stripe-Signature check: HMAC-SHA256 of '<t>.<body>', constant-time compare, replay window."""
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    t = next((v for k, v in parts if k == "t"), "")
    if not t.isdigit() or abs(time.time() - int(t)) > tolerance:
        return False
    want = hmac.new((secret or HOOK).encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(want, v) for k, v in parts if k == "v1")


class Checkout(BaseModel):
    plan: str


def register(app, current_user, rate_limit):
    @app.get("/api/billing/plans")
    def plans():
        return [{"id": k, "tier": k.split(":")[0], "duration": k.split(":")[1]} for k in PRICES] if configured() else []

    @app.post("/api/billing/checkout")
    def checkout(body: Checkout, request: Request, u=Depends(current_user)):
        rate_limit(request, "checkout", 10)
        if not configured() or body.plan not in PRICES:
            raise HTTPException(503, "Payments are not available")
        r = requests.post("https://api.stripe.com/v1/checkout/sessions", auth=(KEY, ""), timeout=20, data={
            "mode": "payment", "line_items[0][price]": PRICES[body.plan], "line_items[0][quantity]": "1",
            "customer_email": u["email"], "metadata[plan]": body.plan, "metadata[email]": u["email"],
            "success_url": storage.BASE_URL + "/studio?paid=1", "cancel_url": storage.BASE_URL + "/studio"})
        if r.status_code != 200:
            raise HTTPException(502, "Payment provider error")
        return {"url": r.json()["url"]}

    @app.post("/api/billing/webhook", include_in_schema=False)
    async def webhook(request: Request):
        payload = await request.body()
        if not configured() or not verify(payload, request.headers.get("stripe-signature", "")):
            raise HTTPException(400, "bad signature")
        ev = json.loads(payload)
        obj = ev.get("data", {}).get("object", {})
        if ev.get("type") == "checkout.session.completed" and obj.get("payment_status") == "paid":
            sid, meta = obj.get("id", ""), obj.get("metadata") or {}
            with db.connect() as c:  # Stripe retries webhooks: one license per checkout session
                seen = c.execute("SELECT 1 FROM activity WHERE action='purchase' AND detail=?", (sid,)).fetchone()
            if not seen and meta.get("plan") in PRICES and meta.get("email"):
                tier, dur = meta["plan"].split(":")
                name = (obj.get("customer_details") or {}).get("name") or meta["email"].split("@")[0]
                db.create_license(name, "(customer)", meta["email"], tier, dur)
                db.log(0, "purchase", "", sid)
                try:
                    mailer.send(meta["email"], "Your Scic3D license is active",
                                f"Thank you! Your {tier} license ({dur}) is ready.\nSign in with Google ({meta['email']}) at "
                                f"{storage.BASE_URL}/studio and it unlocks automatically.")
                except Exception:
                    pass  # the license exists either way
        return {"received": True}
