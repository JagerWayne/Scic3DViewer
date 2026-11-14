"""License generator. Usage: python admin.py new | list | revoke <email> | upgrade <email> <TIER>"""
import sys, time
import db

db.init()
cmd = sys.argv[1] if len(sys.argv) > 1 else "new"

if cmd == "new":
    name = input("Name & Surname: ").strip()
    email = input("Email: ").strip()
    tier = input(f"Tier ({'/'.join(db.TIERS)}) [PRO]: ").strip() or "PRO"
    dur = input("Duration after activation (e.g. 30d, 6m, 1y): ").strip()
    if not (name and "@" in email):
        sys.exit("a name and a valid email are required")
    key = db.create_license(name, email, tier, dur)  # key is shown once; only its hash is stored
    print(f"\nLicense created for {email} ({tier.upper()}, {dur} after first sign-in).\n"
          f"{name} just signs in with the Google account that uses {email}; the license applies automatically.\n"
          f"(Reference key, not needed by the customer: {key})")
elif cmd == "list":
    with db.connect() as c:
        for r in c.execute("SELECT * FROM licenses WHERE duration_days IS NOT NULL ORDER BY id"):
            exp = time.strftime("%Y-%m-%d", time.localtime(r["expires_at"])) if r["expires_at"] else "not activated"
            print(f'{r["id"]:>3} {r["tier"]:<10} {r["email"]:<30} {r["name"]}  expires: {exp}'
                  f'{"  REVOKED" if r["revoked"] else ""}{"  PAUSED" if r["paused_at"] else ""}')
elif cmd in ("revoke", "upgrade") and len(sys.argv) > 2:
    with db.connect() as c:
        if cmd == "revoke":
            n = c.execute("UPDATE licenses SET revoked=1 WHERE email=? AND duration_days IS NOT NULL", (sys.argv[2].lower(),)).rowcount
        else:
            if len(sys.argv) < 4 or sys.argv[3].upper() not in db.TIERS:
                sys.exit("upgrade <email> <FREE|PRO|ENTERPRISE>")
            n = c.execute("UPDATE licenses SET tier=? WHERE email=? AND duration_days IS NOT NULL", (sys.argv[3].upper(), sys.argv[2].lower())).rowcount
    print(f"{n} license(s) updated")
else:
    sys.exit(__doc__)
