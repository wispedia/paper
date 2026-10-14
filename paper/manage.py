import argparse
import json
import os
import secrets
import time
from .db import connect, initialize
from .security import hash_password


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Paper account administration")
    parser.add_argument("command", choices=["create-admin", "reset-password"])
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    initialize()
    password = secrets.token_urlsafe(20)
    with connect() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username=?", (args.username,)).fetchone()
        if args.command == "create-admin":
            if existing:
                raise SystemExit("Account exists; no password has been changed.")
            conn.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)", (args.username, hash_password(password), "admin", int(time.time())))
        else:
            if not existing:
                raise SystemExit("Account does not exist.")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), existing["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (existing["id"],))
    print(json.dumps({"username": args.username, "password": password}, ensure_ascii=False))


if __name__ == "__main__":
    main()
