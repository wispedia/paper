import functools
import hmac
import json
import math
import os
import re
import secrets
import shutil
import time
import uuid
from pathlib import Path

from flask import Flask, g, jsonify, request, send_file, send_from_directory
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import RequestEntityTooLarge

from .db import connect, data_dir, initialize
from .security import current_session, hash_password, new_session, token_hash, verify_password
from .preview import manifest

Image.MAX_IMAGE_PIXELS = 35_000_000
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")


def create_app():
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 13 * 1024 * 1024
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    initialize()
    allowed = set(os.environ.get("PAPER_ALLOWED_ORIGINS", "https://paper.guanchi.ren,https://www.paper.guanchi.ren").split(","))
    frontend = Path(os.environ.get("PAPER_FRONTEND_DIR", "frontend/dist")).resolve()
    secure_cookie = os.environ.get("PAPER_SECURE_COOKIE", "1") == "1"

    def fail(message, status=400):
        return jsonify(error=message), status

    @app.before_request
    def protect():
        g.user = current_session(request.cookies.get("paper_session"))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if not origin or origin not in allowed:
                return fail("请求来源无效，请刷新页面后重试。", 403)
            if request.path != "/api/login":
                if not g.user:
                    return fail("请先登录。", 401)
                supplied_csrf = request.headers.get("X-CSRF-Token", "")
                if not supplied_csrf.isascii() or not hmac.compare_digest(supplied_csrf, g.user["csrf"]):
                    return fail("登录状态已更新，请刷新页面。", 403)
            if request.is_json and not isinstance(request.get_json(silent=True), dict):
                return fail("请求格式无效。")

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def authenticated(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            if not g.user:
                return fail("请先登录。", 401)
            return fn(*args, **kwargs)
        return wrapped

    def admin(fn):
        @functools.wraps(fn)
        @authenticated
        def wrapped(*args, **kwargs):
            if g.user["role"] != "admin":
                return fail("只有管理员可以执行此操作。", 403)
            return fn(*args, **kwargs)
        return wrapped

    @app.get("/healthz")
    def health():
        with connect() as conn:
            conn.execute("SELECT 1")
        return jsonify(status="ok")

    @app.get("/api/me")
    @authenticated
    def me():
        return jsonify(user={k: g.user[k] for k in ["id", "username", "role"]}, csrf=g.user["csrf"])

    @app.post("/api/login")
    def login():
        body = request.get_json(silent=True) or {}
        username, password = body.get("username", ""), body.get("password", "")
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 256:
            return fail("账号或密码错误。", 401)
        now = int(time.time())
        address = request.remote_addr or "unknown"
        with connect() as conn:
            conn.execute("DELETE FROM login_attempts WHERE created_at<?", (now - 900,))
            attempts = conn.execute("SELECT count(*) FROM login_attempts WHERE address=?", (address,)).fetchone()[0]
            if attempts >= 12:
                return fail("登录尝试过于频繁，请 15 分钟后再试。", 429)
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row or not row["active"] or not verify_password(row["password_hash"], password):
                conn.execute("INSERT INTO login_attempts VALUES (?,?)", (address, now))
                return fail("账号或密码错误。", 401)
            conn.execute("DELETE FROM login_attempts WHERE address=?", (address,))
        token, csrf = new_session(row["id"])
        response = jsonify(user={k: row[k] for k in ["id", "username", "role"]}, csrf=csrf)
        response.set_cookie("paper_session", token, httponly=True, secure=secure_cookie, samesite="Lax", max_age=86400, path="/")
        return response

    @app.post("/api/logout")
    @authenticated
    def logout():
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(request.cookies["paper_session"]),))
        response = jsonify(ok=True)
        response.delete_cookie("paper_session", secure=secure_cookie, httponly=True, samesite="Lax", path="/")
        return response

    @app.get("/api/jobs")
    @authenticated
    def jobs():
        with connect() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (g.user["id"],)).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d.pop("usage", None)
            d["warnings"] = json.loads(d["warnings"])
            result.append(d)
        return jsonify(jobs=result, next_request_at=g.user["last_request_at"] + 30)

    def check_request_limit(conn):
        row = conn.execute("SELECT last_request_at FROM users WHERE id=?", (g.user["id"],)).fetchone()
        remaining = row["last_request_at"] + 30 - time.time()
        if remaining > 0:
            retry_after = math.ceil(remaining)
            response = jsonify(error=f"每 30 秒只能提交一次，请 {retry_after} 秒后再试。", retry_after=retry_after)
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response
        pending = conn.execute("SELECT count(*) FROM jobs WHERE user_id=? AND status IN ('queued','processing')", (g.user["id"],)).fetchone()[0]
        if pending:
            return fail("当前账号已有任务在处理，请等待完成后再上传。", 429)
        total = conn.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','processing')").fetchone()[0]
        if total >= 30:
            return fail("队列暂时已满，请稍后再试。", 429)
        return None

    @app.post("/api/jobs")
    @authenticated
    def upload():
        mode = request.form.get("recognition_mode", "standard")
        if mode not in {"standard", "fine"}:
            return fail("请选择标准或精细识别档位。")
        image = request.files.get("image")
        if not image or not image.filename:
            return fail("请选择一张试卷图片。")
        if len(image.filename) > 200:
            return fail("文件名过长。")
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            limited = check_request_limit(conn)
            if limited is not None:
                return limited
            job_id = uuid.uuid4().hex
            directory = data_dir() / "jobs" / job_id
            directory.mkdir(parents=True, mode=0o700)
            try:
                image.stream.seek(0, 2)
                size = image.stream.tell()
                image.stream.seek(0)
                if size > 12 * 1024 * 1024:
                    raise ValueError("图片不能超过 12 MB。")
                with Image.open(image.stream) as opened:
                    if opened.format not in {"JPEG", "PNG", "WEBP"} or getattr(opened, "n_frames", 1) != 1:
                        raise ValueError("请上传 JPG、PNG 或 WebP 静态图片。")
                    if min(opened.size) < 200:
                        raise ValueError("图片尺寸太小，请上传清晰的原图。")
                    if opened.width * opened.height > Image.MAX_IMAGE_PIXELS:
                        raise ValueError("图片像素过大，请缩小到 3500 万像素以内。")
                    opened.thumbnail((3200, 3200))
                    normalized = ImageOps.exif_transpose(opened).convert("RGB")
                    normalized.save(directory / "input.jpg", quality=95, optimize=True)
                conn.execute("INSERT INTO jobs(id,user_id,original_name,created_at,recognition_mode) VALUES (?,?,?,?,?)", (job_id, g.user["id"], Path(image.filename).name, int(time.time()), mode))
                conn.execute("UPDATE users SET last_request_at=? WHERE id=?", (time.time(), g.user["id"]))
            except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                shutil.rmtree(directory, ignore_errors=True)
                return fail(str(exc) if isinstance(exc, ValueError) else "无法读取这张图片，请检查文件后重试。")
        return jsonify(id=job_id), 201

    def owned_job(job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            return None
        with connect() as conn:
            return conn.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, g.user["id"])).fetchone()

    @app.get("/api/jobs/<job_id>/image")
    @authenticated
    def source_image(job_id):
        if not owned_job(job_id):
            return fail("任务不存在。", 404)
        return send_file(data_dir() / "jobs" / job_id / "input.jpg", mimetype="image/jpeg")

    @app.get("/api/jobs/<job_id>/download")
    @authenticated
    def download(job_id):
        row = owned_job(job_id)
        if not row or row["status"] != "completed":
            return fail("文件尚未生成。", 404)
        filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", row["title"] or "试卷")[:80] or "试卷"
        return send_file(data_dir() / "jobs" / job_id / "paper.docx", as_attachment=True, download_name=filename + "_电子版.docx")

    @app.post("/api/jobs/<job_id>/retry")
    @authenticated
    def retry(job_id):
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, g.user["id"])).fetchone()
            if not row or row["status"] != "failed":
                return fail("当前任务不能重试。")
            limited = check_request_limit(conn)
            if limited is not None:
                return limited
            if row["attempts"] >= 3:
                return fail("重试次数已达上限，请重新拍摄清晰图片。", 429)
            conn.execute("UPDATE jobs SET status='queued',stage='等待处理',error=NULL WHERE id=?", (job_id,))
            conn.execute("UPDATE users SET last_request_at=? WHERE id=?", (time.time(), g.user["id"]))
        return jsonify(ok=True)

    @app.get("/api/jobs/<job_id>/preview")
    @authenticated
    def preview_info(job_id):
        row = owned_job(job_id)
        if not row or row["status"] != "completed":
            return fail("文件尚未生成。", 404)
        value = manifest(data_dir() / "jobs" / job_id)
        if not value:
            return jsonify(status="pending", pages=[])
        return jsonify(status=value["status"], pages=[{
            **page, "url": f"/api/jobs/{job_id}/preview/{page['number']}"
        } for page in value["pages"]])

    @app.get("/api/jobs/<job_id>/preview/<int:page_number>")
    @authenticated
    def preview_page(job_id, page_number):
        row = owned_job(job_id)
        if not row or row["status"] != "completed":
            return fail("任务不存在。", 404)
        directory = data_dir() / "jobs" / job_id
        value = manifest(directory)
        if not value or value["status"] != "ready" or not 1 <= page_number <= len(value["pages"]):
            return fail("预览尚未生成。", 404)
        return send_file(directory / "preview" / f"page-{page_number}.png", mimetype="image/png")

    @app.get("/api/admin/users")
    @admin
    def users():
        with connect() as conn:
            rows = conn.execute("SELECT id,username,role,active,created_at FROM users ORDER BY id DESC").fetchall()
        return jsonify(users=[dict(r) for r in rows])

    @app.post("/api/admin/users")
    @admin
    def create_user():
        username = (request.get_json(silent=True) or {}).get("username", "")
        if not isinstance(username, str) or not USERNAME.fullmatch(username):
            return fail("账号需为 3–32 位字母、数字、点、下划线或短横线。")
        password = secrets.token_urlsafe(15)
        with connect() as conn:
            if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                return fail("这个账号已存在。", 409)
            cursor = conn.execute("INSERT INTO users(username,password_hash,created_at) VALUES (?,?,?)", (username, hash_password(password), int(time.time())))
        return jsonify(id=cursor.lastrowid, username=username, password=password), 201

    @app.post("/api/admin/users/<int:user_id>/reset")
    @admin
    def reset_user(user_id):
        password = secrets.token_urlsafe(15)
        with connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row or row["role"] == "admin":
                return fail("请通过服务器管理命令重置管理员密码。")
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return jsonify(username=row["username"], password=password)

    @app.post("/api/admin/users/<int:user_id>/toggle")
    @admin
    def toggle_user(user_id):
        with connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row or row["role"] == "admin":
                return fail("不能停用管理员。")
            conn.execute("UPDATE users SET active=? WHERE id=?", (1 - row["active"], user_id))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return jsonify(ok=True)

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_):
        return fail("图片不能超过 12 MB。", 413)

    @app.get("/")
    @app.get("/<path:path>")
    def frontend_page(path=""):
        if path.startswith("api/"):
            return fail("接口不存在。", 404)
        if path and (frontend / path).is_file():
            return send_from_directory(frontend, path)
        return send_from_directory(frontend, "index.html")

    return app
