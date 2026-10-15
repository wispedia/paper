"""Render the generated Word through LibreOffice, then cache page images."""
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

from PIL import Image

log = logging.getLogger(__name__)
VERSION = 6


def preview_copy(source, target):
    # Writer inserts missing operands for leading '=' and trailing operators.
    # Render only those top-level edge operators as text in a preview copy.
    # Work on XML fragments to preserve every original namespace declaration.
    def formula(match):
        block = match[0]
        def operator(value):
            return '<w:r><w:rPr><w:position w:val="4"/></w:rPr><w:t>' + value + '</w:t></w:r>'
        run = r'<m:r\b[^>]*>(?:(?!<m:r\b).)*?<m:t(?:\s[^>]*)?>{token}</m:t>(?:(?!<m:r\b).)*?</m:r>'
        head = re.match(r'(<m:oMath\b[^>]*>(?:<m:(?:box|e)\b[^>]*>)*)(' + run.format(token='=') + ')', block, re.S)
        before = ""
        if head:
            before = operator("=")
            block = head[1] + block[head.end():]
        tail = re.search('(' + run.format(token='([=+−×÷/])') + r')((?:</m:(?:e|box)>)*</m:oMath>)$', block, re.S)
        if not tail:
            return before + (block if "<m:t" in block else "")
        clean = block[:tail.start(1)] + tail[3]
        return before + (clean if "<m:t" in clean else "") + operator(tail[2])

    with ZipFile(source) as original, ZipFile(target, "w") as copy:
        for item in original.infolist():
            data = original.read(item.filename)
            if item.filename == "word/document.xml":
                text = data.decode("utf-8")
                text = re.sub(r"<m:oMath\b[^>]*>.*?</m:oMath>", formula, text, flags=re.S)
                data = text.encode("utf-8")
            copy.writestr(item, data)


def source_digest(directory):
    return hashlib.sha256((directory / "paper.docx").read_bytes()).hexdigest()


def manifest(directory):
    try:
        value = json.loads((directory / "preview.json").read_text())
        if value.get("version") == VERSION and value.get("source") == source_digest(directory):
            return value
    except (OSError, ValueError):
        pass
    return None


def run(command):
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True, cwd="/", env={**os.environ, "LC_ALL": "C"})
    try:
        output, _ = proc.communicate(timeout=90)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise RuntimeError("Preview render timed out")
    if proc.returncode:
        raise RuntimeError("Preview renderer failed")
    return output.decode(errors="replace")


def ensure_preview(directory):
    directory = Path(directory)
    if manifest(directory):
        return
    try:
        digest = source_digest(directory)
    except OSError:
        return
    value = {"version": VERSION, "source": digest, "status": "failed", "pages": []}
    try:
        office = os.environ.get("PAPER_SOFFICE_BIN", "soffice")
        version = re.search(r"LibreOffice(?:Dev)?\s+(\d+)\.(\d+)", run([office, "--version"]))
        if not version or (int(version[1]), int(version[2])) < (26, 8):
            raise RuntimeError("LibreOffice 26.8 or newer is required for equation fidelity")
        with tempfile.TemporaryDirectory(prefix=".preview-", dir=directory) as temporary:
            temp = Path(temporary)
            preview_copy(directory / "paper.docx", temp / "paper.docx")
            run([office,
                 f"-env:UserInstallation={(temp / 'profile').as_uri()}",
                 "--headless", "--norestore", "--convert-to", "pdf:writer_pdf_Export",
                 "--outdir", str(temp), str(temp / "paper.docx")])
            pdf = temp / "paper.pdf"
            if not pdf.is_file():
                raise RuntimeError("Preview PDF missing")
            info = run([os.environ.get("PAPER_PDFINFO_BIN", "pdfinfo"), str(pdf)])
            count = re.search(r"^Pages:\s+(\d+)", info, re.M)
            if not count or not 1 <= int(count[1]) <= 30:
                raise RuntimeError("Preview page count invalid")
            run([os.environ.get("PAPER_PDFTOPPM_BIN", "pdftoppm"), "-png", "-scale-to", "1680",
                 str(pdf), str(temp / "page")])
            images = sorted(temp.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
            if len(images) != int(count[1]):
                raise RuntimeError("Preview pages missing")
            target = directory / "preview"
            target.mkdir(exist_ok=True, mode=0o700)
            pages = []
            for number, image in enumerate(images, 1):
                with Image.open(image) as opened:
                    width, height = opened.size
                    opened.verify()
                image.replace(target / f"page-{number}.png")
                pages.append({"number": number, "width": width, "height": height})
            value.update(status="ready", pages=pages)
    except Exception as exc:
        log.warning("Preview unavailable for job %s (%s)", directory.name, type(exc).__name__)
    output = directory / "preview.json.tmp"
    output.write_text(json.dumps(value))
    output.replace(directory / "preview.json")


if __name__ == "__main__":
    import sys
    ensure_preview(Path(sys.argv[1]))
