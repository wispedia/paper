import hashlib
import secrets
import time
from werkzeug.security import generate_password_hash, check_password_hash
from .db import connect


def hash_password(password):
    return generate_password_hash(password, method="scrypt:32768:8:1")


def verify_password(stored, password):
    return check_password_hash(stored, password)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(user_id):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = int(time.time())
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute("INSERT INTO sessions VALUES (?,?,?,?)", (token_hash(token), user_id, csrf, now + 86400))
    return token, csrf


def current_session(token):
    if not token:
        return None
    with connect() as conn:
        return conn.execute("""SELECT users.id, username, role, active, last_request_at, sessions.csrf
            FROM sessions JOIN users ON users.id=sessions.user_id
            WHERE token_hash=? AND expires_at>? AND active=1""", (token_hash(token), int(time.time()))).fetchone()
