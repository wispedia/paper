"""A dedicated supervisor; only its renderer containers mount an exam directory."""
import json
import logging
import os
import pwd
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .preview import VERSION, manifest, source_digest

log = logging.getLogger(__name__)


def candidates(base):
    jobs = base / "jobs"
    if jobs.is_symlink() or not jobs.is_dir():
        return
    for directory in jobs.iterdir():
        if not re.fullmatch(r"[a-f0-9]{32}", directory.name) or directory.is_symlink():
            continue
        if directory.resolve() != directory or not directory.is_dir():
            continue
        word = directory / "paper.docx"
        if word.is_file() and not word.is_symlink() and not (directory / "preview.json").is_symlink():
            yield directory


def render(directory, identity):
    name = "paper-preview-" + directory.name
    command = ["docker", "run", "--rm", "--name", name, "--network", "none",
               "--memory", "512m", "--cpus", "1", "--pids-limit", "80",
               "--user", f"{identity.pw_uid}:{identity.pw_gid}", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--read-only",
               "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
               "--mount", f"type=bind,src={directory},dst=/work",
               "--mount", f"type=bind,src={Path(__file__).with_name('preview.py').resolve()},dst=/opt/paper/preview.py,readonly",
               "paper-preview:26.8.1"]
    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        proc.wait(timeout=240)
        if proc.returncode or not manifest(directory):
            raise RuntimeError("Preview container failed")
    except Exception as exc:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        value = {"version": VERSION, "source": source_digest(directory), "status": "failed", "pages": []}
        with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as temp:
            json.dump(value, temp)
            os.fchown(temp.fileno(), identity.pw_uid, identity.pw_gid)
            temporary = temp.name
        os.replace(temporary, directory / "preview.json")
        log.warning("Preview failed: %s (%s)", directory.name, type(exc).__name__)
    finally:
        subprocess.run(["docker", "rm", "--force", name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15)


def main():
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    base = Path(os.environ.get("PAPER_DATA_DIR", "/var/lib/paper"))
    if not base.is_absolute() or base.resolve() != base:
        raise RuntimeError("Preview data directory must not be a symlink")
    identity = pwd.getpwnam("paper")
    while True:
        try:
            for directory in candidates(base):
                if not manifest(directory):
                    render(directory, identity)
                    log.info("Preview processed: %s", directory.name)
                    break
        except Exception as exc:
            log.warning("Preview supervisor error (%s)", type(exc).__name__)
        time.sleep(3)


if __name__ == "__main__":
    main()
