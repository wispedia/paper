import fcntl
import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from PIL import Image

from jsonschema import validate
from .db import connect, data_dir, initialize
from .document import build_document, build_handwriting_document
from . import handwriting
from .schema import SCHEMA, PROMPT

log = logging.getLogger("paper.worker")


def input_images(directory, handwritten=False, layout="single"):
    original = directory / "input.jpg"
    if handwritten:
        if layout != "double":
            return [original]
        with Image.open(original) as image:
            middle = image.width // 2
            paths = []
            for name, box in [("left", (0, 0, middle, image.height)), ("right", (middle, 0, image.width, image.height))]:
                path = directory / f"handwriting-{name}.png"
                image.crop(box).save(path)
                paths.append(path)
        return paths
    paths = [original]
    if os.environ.get("PAPER_IMAGE_ZOOM", "0") != "1":
        return paths
    with Image.open(original) as image:
        w, h = image.size
        for index, (left, top, right, bottom) in enumerate([
            (0, 0, .55, .55), (.45, 0, 1, .55),
            (0, .45, .55, 1), (.45, .45, 1, 1),
        ]):
            path = directory / f"zoom-{index}.jpg"
            image.crop((int(w*left), int(h*top), int(w*right), int(h*bottom))).save(path, quality=96)
            paths.append(path)
    return paths


def update(job_id, **values):
    with connect() as conn:
        sql = ",".join(f"{key}=?" for key in values)
        conn.execute(f"UPDATE jobs SET {sql} WHERE id=?", (*values.values(), job_id))


def claim():
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if row:
            conn.execute("UPDATE jobs SET status='processing',stage='正在识别试卷',started_at=?,attempts=attempts+1 WHERE id=?", (int(time.time()), row["id"]))
        return row


def process(job):
    job_id = job["id"]
    directory = data_dir() / "jobs" / job_id
    handwritten = job["recognition_type"] == "handwritten"
    contract = handwriting.SCHEMA if handwritten else SCHEMA
    schema = directory / "schema.json"
    schema.write_text(json.dumps(contract, ensure_ascii=False))
    result = directory / "result.json"
    result.unlink(missing_ok=True)
    effort = "medium" if job["recognition_mode"] == "standard" else "high"
    command = [os.environ.get("PAPER_CODEX_BIN", "codex"), "exec",
        "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
        "--sandbox", "read-only", "--disable", "shell_tool", "--disable", "multi_agent",
        "--disable", "apps", "--disable", "plugins", "--disable", "hooks",
        "--disable", "browser_use", "--disable", "computer_use", "--disable", "image_generation",
        "--disable", "sleep_tool", "--disable", "goals",
        "-c", 'web_search="disabled"', "-c", 'approval_policy="never"',
        "-c", f'model_reasoning_effort="{effort}"', "-c", 'service_tier="default"',
        "--model", os.environ.get("PAPER_CODEX_MODEL", "gpt-6.1-sol"),
        "--cd", str(directory)]
    try:
        images = input_images(directory, handwritten, job["page_layout"])
        command += ["--image", *[str(p) for p in images],
                    "--json", "--output-schema", str(schema), "--output-last-message", str(result), "-"]
        with (directory / "events.jsonl").open("w") as events, (directory / "stderr.log").open("w") as errors:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=events, stderr=errors, text=True, start_new_session=True)
            try:
                if handwritten:
                    prompt = handwriting.PROMPT
                    if len(images) == 2:
                        prompt += "\n两张附图是同一张图片裁开的左页、右页，按左页后右页阅读，不重复内容。"
                else:
                    zoom_note = "\n第一张附图是完整试卷，其余附图是同一页的局部放大，仅用于核对小字，不要重复题目。所有配图 bbox 仍相对于第一张完整试卷。" if os.environ.get("PAPER_IMAGE_ZOOM", "0") == "1" else ""
                    prompt = PROMPT + zoom_note
                proc.communicate(prompt, timeout=int(os.environ.get("PAPER_JOB_TIMEOUT", "600")))
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise TimeoutError("识别超时，请稍后重试。")
        if proc.returncode != 0 or not result.exists():
            raise RuntimeError("识别服务暂时不可用，请稍后重试。")
        content = json.loads(result.read_text())
        validate(content, contract)
        questions = len(content["answers"]) if handwritten else sum(len(sec["questions"]) for sec in content["sections"])
        if not questions or questions > 100:
            raise ValueError("没有识别到手写内容，请上传清晰的手写图片。" if handwritten else "没有识别到试卷题目，请上传清晰的试卷照片。")
        if len(result.read_bytes()) > 300_000:
            raise ValueError("识别结果过长，请按页分别上传。")
        update(job_id, stage="正在生成 Word")
        if handwritten:
            build_handwriting_document(content, directory)
        else:
            build_document(content, directory)
        usage = None
        for line in (directory / "events.jsonl").read_text().splitlines():
            try:
                event = json.loads(line)
                if event.get("type") == "turn.completed":
                    usage = json.dumps(event.get("usage", {}))
            except ValueError:
                continue
        update(job_id, status="completed", stage="生成完成", title=content["title"][:100],
            warnings=json.dumps(handwriting.warnings(content) if handwritten else content["uncertainties"], ensure_ascii=False),
            usage=usage, finished_at=int(time.time()), error=None)
        log.info("Job completed: %s", job_id)
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, TimeoutError, RuntimeError)) else "生成文件失败，请稍后重试。"
        update(job_id, status="failed", stage="处理失败", error=message, finished_at=int(time.time()))
        log.error("Job failed: %s (%s)", job_id, type(exc).__name__)


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    initialize()
    lock = (data_dir() / "worker.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with connect() as conn:
        conn.execute("UPDATE jobs SET status='failed',stage='处理已中断',error='服务重启导致任务中断，可以点击重试。' WHERE status='processing'")
    while True:
        job = claim()
        if job:
            process(job)
        else:
            time.sleep(2)


if __name__ == "__main__":
    main()
