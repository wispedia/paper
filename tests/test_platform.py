import io
import json
import time
import uuid
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile

import pytest
from PIL import Image
from lxml import etree

from paper.app import create_app
from paper.db import connect, data_dir
from paper.security import hash_password
from paper.document import build_document
from paper.worker import claim, process
from paper.preview import ensure_preview, source_digest, VERSION

ORIGIN = "http://localhost"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_SECURE_COOKIE", "0")
    monkeypatch.setenv("PAPER_ALLOWED_ORIGINS", ORIGIN)
    app = create_app()
    app.config["TESTING"] = True
    with connect() as conn:
        for username, role in [("admin", "admin"), ("alice", "user"), ("bobby", "user")]:
            conn.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)", (username, hash_password("test-password"), role, int(time.time())))
    return app


def login(client, username="alice"):
    r = client.post("/api/login", json={"username": username, "password": "test-password"}, headers={"Origin": ORIGIN})
    assert r.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": r.json["csrf"]}


def image():
    buf = io.BytesIO()
    Image.new("RGB", (500, 600), "white").save(buf, format="PNG")
    buf.seek(0)
    return buf


def upload(client, headers):
    return client.post("/api/jobs", data={"image": (image(), "exam.png")}, headers=headers)


def test_login_csrf_and_no_registration(app):
    c = app.test_client()
    assert c.get("/api/jobs").status_code == 401
    assert c.post("/api/login", json={"username": "alice", "password": "test-password"}, headers={"Origin": "https://evil.example"}).status_code == 403
    headers = login(c)
    assert c.post("/api/logout", headers={"Origin": ORIGIN}).status_code == 403
    assert c.post("/api/register", headers=headers).status_code == 404 or c.post("/api/register", headers=headers).status_code == 405
    assert c.post("/api/logout", headers=headers).status_code == 200
    assert c.get("/api/me").status_code == 401


def test_malformed_json_and_nonce_rejected(app):
    c = app.test_client()
    assert c.post("/api/login", json=[], headers={"Origin": ORIGIN}).status_code == 400
    login(c)
    assert c.post("/api/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": "é"}).status_code == 403


def test_admin_password_recovery_revokes_existing_session(app):
    c = app.test_client()
    login(c, "admin")
    result = subprocess.run([sys.executable, "-m", "paper.manage", "reset-password", "--username", "admin"], capture_output=True, text=True, check=True)
    password = json.loads(result.stdout)["password"]
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/login", json={"username": "admin", "password": "test-password"}, headers={"Origin": ORIGIN}).status_code == 401
    assert c.post("/api/login", json={"username": "admin", "password": password}, headers={"Origin": ORIGIN}).status_code == 200


def test_accounts_permissions_and_revocation(app):
    ordinary = app.test_client()
    headers = login(ordinary)
    assert ordinary.get("/api/admin/users").status_code == 403
    assert ordinary.post("/api/admin/users", json={"username": "teacher"}, headers=headers).status_code == 403
    admin = app.test_client()
    ah = login(admin, "admin")
    created = admin.post("/api/admin/users", json={"username": "teacher"}, headers=ah)
    assert created.status_code == 201 and len(created.json["password"]) >= 20
    tc = app.test_client()
    assert tc.post("/api/login", json={"username": "teacher", "password": created.json["password"]}, headers={"Origin": ORIGIN}).status_code == 200
    assert admin.post(f"/api/admin/users/{created.json['id']}/toggle", headers=ah).status_code == 200
    assert tc.get("/api/me").status_code == 401
    assert admin.post("/api/admin/users/1/toggle", headers=ah).status_code == 400


def test_upload_rate_limit_active_job_and_ownership(app):
    alice, bobby = app.test_client(), app.test_client()
    ah, bh = login(alice), login(bobby, "bobby")
    created = upload(alice, ah)
    assert created.status_code == 201
    job_id = created.json["id"]
    limited = upload(alice, ah)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0
    assert bobby.get(f"/api/jobs/{job_id}/image").status_code == 404
    assert bobby.get(f"/api/jobs/{job_id}/download").status_code == 404
    assert bobby.get("/api/jobs").json["jobs"] == []
    with connect() as conn:
        conn.execute("UPDATE users SET last_request_at=0 WHERE username='alice'")
    assert upload(alice, ah).status_code == 429
    assert upload(bobby, bh).status_code == 201
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id,))
    assert alice.post(f"/api/jobs/{job_id}/retry", headers=ah).status_code == 200
    assert alice.post(f"/api/jobs/{job_id}/retry", headers=ah).status_code == 400


def test_concurrent_uploads_only_accept_one(app):
    primary = app.test_client()
    headers = login(primary)
    cookie = primary.get_cookie("paper_session").value
    def submit(_):
        c = app.test_client()
        c.set_cookie("paper_session", cookie)
        return upload(c, headers).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(submit, range(2)))
    assert statuses == [201, 429]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_full_thirty_seconds_required(app, monkeypatch):
    clock = [1000.9]
    monkeypatch.setattr("paper.app.time.time", lambda: clock[0])
    c = app.test_client()
    headers = login(c)
    assert upload(c, headers).status_code == 201
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='completed'")
    clock[0] = 1030.1
    assert upload(c, headers).status_code == 429
    clock[0] = 1030.9
    assert upload(c, headers).status_code == 201


def test_invalid_image_does_not_consume_slot(app):
    c = app.test_client()
    headers = login(c)
    bad = c.post("/api/jobs", data={"image": (io.BytesIO(b"invalid"), "invalid.png")}, headers=headers)
    assert bad.status_code == 400
    assert upload(c, headers).status_code == 201


def test_login_throttle(app):
    c = app.test_client()
    for _ in range(12):
        assert c.post("/api/login", json={"username": "alice", "password": "wrong"}, headers={"Origin": ORIGIN}).status_code == 401
    assert c.post("/api/login", json={"username": "alice", "password": "wrong"}, headers={"Origin": ORIGIN}).status_code == 429


def content():
    return {"title": "函数试卷", "subtitle": "满分 100 分", "sections": [{"heading": "一、选择题", "instructions": "选择正确答案。", "questions": [{"number": "1", "stem": r"若 $1<a<2$，则 $\sqrt[3]{(1-a)^3}+\sqrt[4]{(a-2)^4}$ 的结果是（　）。", "options": [{"label": "A", "text": "$1$"}], "subquestions": [r"已知 $f(x)=\begin{cases}x^2,&x\le0\\2^x-2,&x>0\end{cases}$。"], "figures": []}]}], "uncertainties": []}


def test_word_has_editable_math_and_chinese(tmp_path):
    path = build_document(content(), tmp_path)
    with ZipFile(path) as z:
        xml = z.read("word/document.xml")
        root = etree.fromstring(xml)
        ns = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}
        assert len(root.findall(".//m:oMath", ns)) == 4
        assert root.find(".//m:rad", ns) is not None
        assert root.find(".//m:m", ns) is not None
        assert "函数试卷" in xml.decode()
        assert "\\sqrt" not in xml.decode()


def test_nested_square_roots_have_required_omml_degree(tmp_path):
    data = content()
    data["sections"][0]["questions"][0]["stem"] = r"$\left(\frac{1}{6\sqrt{6}}\right)^{-\frac{1}{3}}-\frac{\sqrt{3}+\sqrt{2}}{\sqrt{3}-\sqrt{2}}+6^{0.5}+10^{3\lg 3}$"
    with ZipFile(build_document(data, tmp_path)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        ns = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}
        radicals = root.findall(".//m:rad", ns)
        assert len(radicals) == 5
        for radical in radicals:
            assert radical.find("m:deg", ns) is not None
            assert radical.find("m:radPr/m:degHide", ns).get("{http://schemas.openxmlformats.org/officeDocument/2006/math}val") == "1"


def test_worker_generates_downloadable_word(app, tmp_path, monkeypatch):
    c = app.test_client()
    headers = login(c)
    job_id = upload(c, headers).json["id"]
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(content(), ensure_ascii=False))
    fake = tmp_path / "fake-codex"
    fake.write_text("#!/usr/bin/env python3\nimport json,sys,pathlib\na=sys.argv\np=pathlib.Path(a[a.index('--output-last-message')+1])\np.write_text(pathlib.Path(" + repr(str(fixture)) + ").read_text())\nprint(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':200}}))\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PAPER_CODEX_BIN", str(fake))
    job = claim()
    assert job["id"] == job_id
    process(job)
    assert c.get("/api/jobs").json["jobs"][0]["status"] == "completed"
    result = c.get(f"/api/jobs/{job_id}/download")
    assert result.status_code == 200 and result.data.startswith(b"PK")
    assert "attachment" in result.headers["Content-Disposition"]


def test_worker_failure_can_be_retried(app, tmp_path, monkeypatch):
    c = app.test_client()
    headers = login(c)
    job_id = upload(c, headers).json["id"]
    fake = tmp_path / "broken-codex"
    fake.write_text("#!/bin/sh\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PAPER_CODEX_BIN", str(fake))
    process(claim())
    assert c.get("/api/jobs").json["jobs"][0]["status"] == "failed"
    assert c.post(f"/api/jobs/{job_id}/retry", headers=headers).status_code == 429
    with connect() as conn:
        conn.execute("UPDATE users SET last_request_at=0")
    assert c.post(f"/api/jobs/{job_id}/retry", headers=headers).status_code == 200


def test_preview_is_private_and_invalidates_when_word_changes(app):
    owner, other = app.test_client(), app.test_client()
    headers = login(owner)
    login(other, "bobby")
    job_id = upload(owner, headers).json["id"]
    base = f"/api/jobs/{job_id}/preview"
    assert owner.get(base).status_code == 404
    assert other.get(base).status_code == 404
    assert app.test_client().get(base).status_code == 401
    directory = data_dir() / "jobs" / job_id
    build_document(content(), directory)
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='completed' WHERE id=?", (job_id,))
    assert owner.get(base).json["status"] == "pending"
    (directory / "preview").mkdir()
    Image.new("RGB", (30, 40), "white").save(directory / "preview/page-1.png")
    (directory / "preview.json").write_text(json.dumps({"version": VERSION, "source": source_digest(directory), "status": "ready", "pages": [{"number": 1, "width": 30, "height": 40}]}))
    assert owner.get(base).json["pages"][0]["url"] == base + "/1"
    assert owner.get(base + "/1").mimetype == "image/png"
    assert owner.get(base + "/1").headers["Cache-Control"] == "no-store"
    assert other.get(base + "/1").status_code == 404
    assert app.test_client().get(base + "/1").status_code == 401
    assert owner.get(base + "/0").status_code == 404
    assert owner.get(base + "/2").status_code == 404
    data = content()
    data["title"] = "更新的试卷"
    build_document(data, directory)
    assert owner.get(base).json["status"] == "pending"
    assert owner.get(base + "/1").status_code == 404


def test_preview_failure_preserves_download_and_is_cached(app, monkeypatch):
    owner = app.test_client()
    job_id = upload(owner, login(owner)).json["id"]
    directory = data_dir() / "jobs" / job_id
    build_document(content(), directory)
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='completed' WHERE id=?", (job_id,))
    calls = []
    def broken(command):
        calls.append(command)
        raise RuntimeError("Renderer not installed")
    monkeypatch.setattr("paper.preview.run", broken)
    ensure_preview(directory)
    ensure_preview(directory)
    assert len(calls) == 1
    assert owner.get(f"/api/jobs/{job_id}/preview").json["status"] == "failed"
    assert owner.get(f"/api/jobs/{job_id}/download").status_code == 200
    assert owner.get("/api/jobs").json["jobs"][0]["status"] == "completed"


def test_preview_rejects_excessive_page_count(tmp_path, monkeypatch):
    build_document(content(), tmp_path)
    calls = []
    def renderer(command):
        calls.append(command)
        if "--version" in command:
            return "LibreOffice 26.8.1"
        if "--convert-to" in command:
            from pathlib import Path
            (Path(command[command.index("--outdir") + 1]) / "paper.pdf").write_bytes(b"%PDF")
            return ""
        return "Pages: 31\n"
    monkeypatch.setattr("paper.preview.run", renderer)
    ensure_preview(tmp_path)
    assert len(calls) == 3
    assert json.loads((tmp_path / "preview.json").read_text())["status"] == "failed"


def test_preview_rejects_old_office_equation_renderer(tmp_path, monkeypatch):
    build_document(content(), tmp_path)
    calls = []
    def old_office(command):
        calls.append(command)
        return "LibreOffice 7.1.8.1"
    monkeypatch.setattr("paper.preview.run", old_office)
    ensure_preview(tmp_path)
    assert len(calls) == 1
    assert json.loads((tmp_path / "preview.json").read_text())["status"] == "failed"


def test_preview_supervisor_rejects_symlink_jobs_and_files(tmp_path):
    from paper.preview_service import candidates
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    good = jobs / ("a" * 32)
    good.mkdir()
    (good / "paper.docx").write_bytes(b"word")
    (jobs / ("b" * 32)).symlink_to(good, target_is_directory=True)
    linked = jobs / ("c" * 32)
    linked.mkdir()
    (linked / "paper.docx").symlink_to(good / "paper.docx")
    assert list(candidates(tmp_path)) == [good]


def test_preview_copy_preserves_word_and_avoids_missing_operand_symbols(tmp_path):
    from paper.preview import preview_copy
    data = content()
    data["sections"][0]["questions"][0]["stem"] = r"若 $a=$____，$\frac{1}{2}+x=$____，且 $x=2$。"
    original = build_document(data, tmp_path)
    before = original.read_bytes()
    copy = tmp_path / "render-only.docx"
    preview_copy(original, copy)
    assert original.read_bytes() == before
    with ZipFile(copy) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        ns = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math", "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        assert sum(node.text == "=" for node in root.findall(".//w:t", ns)) == 2
        assert sum(node.text == "=" for node in root.findall(".//m:t", ns)) == 2
        assert root.find(".//m:f", ns) is not None
