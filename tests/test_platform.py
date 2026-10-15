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
from paper.db import connect, data_dir, initialize
from paper.security import hash_password
from paper.document import build_document, build_handwriting_document
from paper.worker import claim, process, input_images
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


def upload(client, headers, mode=None):
    data = {"image": (image(), "exam.png")}
    if mode is not None:
        data["recognition_mode"] = mode
    return client.post("/api/jobs", data=data, headers=headers)


@pytest.mark.parametrize("mode", ["standard", "fine"])
def test_recognition_mode_is_saved_and_retained_on_retry(app, mode):
    client = app.test_client()
    headers = login(client)
    job_id = upload(client, headers, mode).json["id"]
    assert client.get("/api/jobs").json["jobs"][0]["recognition_mode"] == mode
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id,))
        conn.execute("UPDATE users SET last_request_at=0")
    assert client.post(f"/api/jobs/{job_id}/retry", json={"recognition_mode": "other"}, headers=headers).status_code == 200
    assert claim()["recognition_mode"] == mode


def test_invalid_mode_does_not_consume_submission_allowance(app):
    client = app.test_client()
    headers = login(client)
    assert upload(client, headers, "low").status_code == 400
    assert upload(client, headers).status_code == 201
    assert client.get("/api/jobs").json["jobs"][0]["recognition_mode"] == "standard"


def test_existing_jobs_migrate_to_historical_high_mode(app):
    with connect() as conn:
        conn.execute("ALTER TABLE jobs DROP COLUMN recognition_mode")
        conn.execute("INSERT INTO jobs(id,user_id,original_name,created_at) VALUES ('legacy',1,'old.jpg',0)")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: initialize(), range(2)))
    initialize()
    with connect() as conn:
        assert conn.execute("SELECT recognition_mode FROM jobs WHERE id='legacy'").fetchone()[0] == "fine"


@pytest.mark.parametrize(("size", "layout", "expected"), [
    ((1001, 700), "auto", "double"),
    ((600, 900), "auto", "single"),
    ((1001, 700), "single", "double"),
    ((900, 1100), "double", "single"),
])
@pytest.mark.parametrize("mode", ["standard", "fine"])
def test_handwriting_automatically_selects_layout_and_retains_effort_on_retry(app, size, layout, expected, mode):
    c = app.test_client()
    headers = login(c)
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, "PNG")
    buf.seek(0)
    response = c.post("/api/jobs", data={"image": (buf, "handwriting.png"), "recognition_type": "handwritten", "recognition_mode": mode, "page_layout": layout}, headers=headers)
    assert response.status_code == 201
    job_id = response.json["id"]
    saved = c.get("/api/jobs").json["jobs"][0]
    assert (saved["recognition_type"], saved["recognition_mode"], saved["page_layout"]) == ("handwritten", mode, expected)
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id,))
        conn.execute("UPDATE users SET last_request_at=0")
    assert c.post(f"/api/jobs/{job_id}/retry", json={"recognition_type": "printed", "page_layout": "other"}, headers=headers).status_code == 200
    job = claim()
    assert (job["recognition_type"], job["recognition_mode"], job["page_layout"]) == ("handwritten", mode, expected)


def test_handwriting_fields_validate_before_rate_limit_and_old_uploads_remain_printed(app):
    c = app.test_client()
    headers = login(c)
    for fields in [{"recognition_type": "unknown"}, {"recognition_type": "handwritten", "recognition_mode": "low"}]:
        assert c.post("/api/jobs", data={"image": (image(), "exam.png"), **fields}, headers=headers).status_code == 400
    assert upload(c, headers, "fine").status_code == 201
    saved = c.get("/api/jobs").json["jobs"][0]
    assert (saved["recognition_type"], saved["page_layout"], saved["recognition_mode"]) == ("printed", "single", "fine")


def test_old_jobs_migrate_to_printed_single_page(app):
    with connect() as conn:
        conn.execute("ALTER TABLE jobs DROP COLUMN recognition_type")
        conn.execute("ALTER TABLE jobs DROP COLUMN page_layout")
        conn.execute("INSERT INTO jobs(id,user_id,original_name,created_at) VALUES ('old-print',1,'old.jpg',0)")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: initialize(), range(2)))
    initialize()
    with connect() as conn:
        row = conn.execute("SELECT recognition_type,page_layout FROM jobs WHERE id='old-print'").fetchone()
        assert tuple(row) == ("printed", "single")


def test_split_handwriting_preserves_all_pixels_and_never_uses_printed_zoom(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_IMAGE_ZOOM", "1")
    source = Image.new("RGB", (1001, 700), "white")
    source.paste("black", (497, 0, 505, 700))
    source.save(tmp_path / "input.jpg")
    paths = input_images(tmp_path, True, "double")
    assert [p.name for p in paths] == ["handwriting-left.png", "handwriting-right.png"]
    with Image.open(paths[0]) as left, Image.open(paths[1]) as right, Image.open(tmp_path / "input.jpg") as original:
        assert left.size == (500, 700) and right.size == (501, 700)
        joined = Image.new("RGB", original.size)
        joined.paste(left, (0, 0))
        joined.paste(right, (left.width, 0))
        assert joined.tobytes() == original.tobytes()
    assert input_images(tmp_path, True, "single") == [tmp_path / "input.jpg"]


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


def handwritten_content():
    return {"title": "手写内容电子版", "subtitle": "", "answers": [{"question": "13", "parts": [{"label": "（1）", "text": r"$v_2=-3\mathrm{m/s}$" + "\n" + r"$a=\frac{-3-4}{0.05}=140\mathrm{m/s^2}$" + "\n" + r"$\bar v=\frac{x}{t}$"}], "crossed_out": [r"$v_1-v_2$"], "uncertainties": ["覆盖的下标需核对。"]}], "notes": ["部分手写过程需对照原图。"]}


def test_handwritten_word_reflows_parts_without_discarded_drafts(tmp_path):
    data = handwritten_content()
    data["answers"][0]["crossed_out"].append("被删除草稿标记")
    data["answers"][0]["parts"].append({"label": "（2）", "text": "$f(x)=\\begin{cases}x^2,&x\\le0\\\\\n2x,&x>0\\end{cases}$"})
    data["answers"][0]["parts"].append({"label": "（3）", "text": "这是同一段说明，\n不应按手写位置硬换行。"})
    data["answers"][0]["parts"].append({"label": "（4）", "text": "$x=$【待核对】$\\mathrm{m}$。\n$x_1=8\\mathrm{m}$。"})
    with ZipFile(build_handwriting_document(data, tmp_path)) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math", "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    text = "".join(root.itertext())
    assert "140" in text and "−140" not in text
    assert "已划掉" not in text and "被删除草稿标记" not in text
    assert text.index("140") < text.index("待核对内容")
    paragraphs = root.findall(".//w:body/w:p", ns)
    first_part = next(p for p in paragraphs if "（1）" in "".join(p.itertext()))
    assert first_part.find(".//m:oMath", ns) is not None
    assert first_part.find("w:pPr/w:keepNext", ns) is not None
    assert not any("".join(p.itertext()).strip() in {"（1）", "（2）"} for p in paragraphs)
    prose = next(p for p in paragraphs if "这是同一段说明" in "".join(p.itertext()))
    assert "这是同一段说明，不应按手写位置硬换行。" in "".join(prose.itertext())
    assert prose.find(".//w:br", ns) is None
    uncertain = next(p for p in paragraphs if "（4）" in "".join(p.itertext()))
    assert "【待核对】" in "".join(uncertain.itertext())
    assert "8" not in "".join(uncertain.itertext())
    assert "第13题：覆盖的下标需核对。" in text
    assert "班级：" not in text and "部分手写过程需对照原图。" in text
    assert root.find(".//m:f", ns) is not None and root.find(".//m:sSup", ns) is not None
    group = root.find(".//m:groupChr", ns)
    assert group.find("m:groupChrPr/m:chr", ns).get("{http://schemas.openxmlformats.org/officeDocument/2006/math}val") == "¯"
    assert group.find("m:e", ns) is not None
    assert root.find(".//m:m", ns) is not None and "\\begin" not in text


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize(("mode", "effort"), [("standard", "medium"), ("fine", "high")])
def test_handwriting_worker_uses_selected_effort_and_split_pages(app, tmp_path, monkeypatch, empty, mode, effort):
    c = app.test_client()
    headers = login(c)
    buf = io.BytesIO()
    Image.new("RGB", (1001, 700), "white").save(buf, "PNG")
    buf.seek(0)
    response = c.post("/api/jobs", data={"image": (buf, "answers.png"), "recognition_type": "handwritten", "recognition_mode": mode}, headers=headers)
    assert response.status_code == 201
    job_id = response.json["id"]
    fixture = tmp_path / "handwritten-fixture.json"
    data = handwritten_content()
    del data["answers"][0]["crossed_out"]
    if empty:
        data["answers"] = []
    fixture.write_text(json.dumps(data, ensure_ascii=False))
    arguments, prompt = tmp_path / "handwritten-args.json", tmp_path / "handwritten-prompt.txt"
    fake = tmp_path / "handwritten-codex"
    fake.write_text("#!/usr/bin/env python3\nimport json,sys,pathlib\na=sys.argv\npathlib.Path(" + repr(str(arguments)) + ").write_text(json.dumps(a))\npathlib.Path(" + repr(str(prompt)) + ").write_text(sys.stdin.read())\npathlib.Path(a[a.index('--output-last-message')+1]).write_text(pathlib.Path(" + repr(str(fixture)) + ").read_text())\nprint(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':200}}))\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PAPER_CODEX_BIN", str(fake))
    monkeypatch.setenv("PAPER_IMAGE_ZOOM", "1")
    process(claim())
    args = json.loads(arguments.read_text())
    assert f'model_reasoning_effort="{effort}"' in args
    paths = args[args.index("--image") + 1:args.index("--json")]
    assert len(paths) == 2 and paths[0].endswith("handwriting-left.png") and paths[1].endswith("handwriting-right.png")
    assert "手写" in prompt.read_text() and "左页、右页" in prompt.read_text()
    assert "忽略手写答案" not in prompt.read_text()
    saved = c.get("/api/jobs").json["jobs"][0]
    if empty:
        assert saved["status"] == "failed" and "没有识别到手写内容" in saved["error"]
        assert c.get(f"/api/jobs/{job_id}/download").status_code == 404
        return
    assert saved["status"] == "completed"
    assert "第13题：覆盖的下标需核对。" in saved["warnings"]
    assert c.get(f"/api/jobs/{job_id}/download").data.startswith(b"PK")


def test_failed_page_preprocessing_marks_job_failed_instead_of_stopping_worker(app, monkeypatch):
    c = app.test_client()
    headers = login(c)
    job_id = c.post("/api/jobs", data={"image": (image(), "answers.png"), "recognition_type": "handwritten"}, headers=headers).json["id"]
    def broken(*args):
        raise OSError("Cannot write page crop")
    monkeypatch.setattr("paper.worker.input_images", broken)
    process(claim())
    assert c.get("/api/jobs").json["jobs"][0]["status"] == "failed"
    assert c.get(f"/api/jobs/{job_id}/download").status_code == 404


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


@pytest.mark.parametrize(("mode", "effort"), [("standard", "medium"), ("fine", "high")])
def test_worker_generates_downloadable_word(app, tmp_path, monkeypatch, mode, effort):
    c = app.test_client()
    headers = login(c)
    job_id = upload(c, headers, mode).json["id"]
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(content(), ensure_ascii=False))
    fake = tmp_path / "fake-codex"
    arguments = tmp_path / "arguments.json"
    fake.write_text("#!/usr/bin/env python3\nimport json,sys,pathlib\na=sys.argv\npathlib.Path(" + repr(str(arguments)) + ").write_text(json.dumps(a))\np=pathlib.Path(a[a.index('--output-last-message')+1])\np.write_text(pathlib.Path(" + repr(str(fixture)) + ").read_text())\nprint(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':200}}))\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PAPER_CODEX_BIN", str(fake))
    job = claim()
    assert job["id"] == job_id
    process(job)
    assert f'model_reasoning_effort="{effort}"' in json.loads(arguments.read_text())
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


def test_preview_preserves_leading_equal_and_unfinished_draft_without_placeholders(tmp_path):
    from paper.preview import preview_copy
    data = content()
    data["sections"][0]["questions"][0]["stem"] = r"$=125-62.5$，草稿 $v_0+$，保留 $x^{+}$ 和 $-5$。"
    original = build_document(data, tmp_path)
    before = original.read_bytes()
    copy = tmp_path / "edge-operators.docx"
    preview_copy(original, copy)
    assert original.read_bytes() == before
    with ZipFile(copy) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    ns = {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math", "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    assert [t.text for t in root.findall(".//w:t", ns)].count("=") == 1
    assert [t.text for t in root.findall(".//w:t", ns)].count("+") == 1
    assert root.find(".//m:sSup/m:sup/m:box/m:e/m:r/m:t", ns).text == "+"
    assert "−5" in "".join(root.itertext())
