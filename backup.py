"""Consistent backup of the database + the encryption key (without secret.key the stored Google tokens are unreadable).
Usage: python backup.py [how-many-backups-to-keep, default 14]. Keep the zips somewhere safe, they contain secrets."""
import sqlite3, sys, tempfile, time, zipfile
from pathlib import Path

import db

keep = int(sys.argv[1]) if len(sys.argv) > 1 else 14
out = Path(__file__).parent / "backups"
out.mkdir(exist_ok=True)
name = out / f"scic-{time.strftime('%Y%m%d-%H%M%S')}.zip"
with tempfile.TemporaryDirectory() as t:
    snap = Path(t) / "scic.db"
    src, dst = db.connect(), sqlite3.connect(snap)
    src.backup(dst)  # safe while the server is running
    src.close(); dst.close()
    with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(snap, "scic.db")
        if (db.DATA / "secret.key").exists():
            z.write(db.DATA / "secret.key", "secret.key")
for old in sorted(out.glob("scic-*.zip"))[:-keep]:
    old.unlink()
print("Backup written:", name)
