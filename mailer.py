"""Tiny SMTP sender. Configure with SMTP_HOST, SMTP_PORT (587 STARTTLS or 465 SSL), SMTP_USER, SMTP_PASSWORD, SMTP_FROM."""
import os, smtplib, ssl
from email.message import EmailMessage


def configured() -> bool:
    return bool(os.environ.get("SMTP_HOST"))


def send(to: str, subject: str, body: str):
    if not configured():
        raise RuntimeError("Email is not configured (set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)")
    user, host, port = os.environ.get("SMTP_USER", ""), os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ.get("SMTP_FROM", user), to, subject
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if port == 465:
        s = smtplib.SMTP_SSL(host, port, context=ctx, timeout=20)
    else:
        s = smtplib.SMTP(host, port, timeout=20); s.starttls(context=ctx)
    with s:
        if user:
            s.login(user, os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
