"""原子写入 JSON，以及“每期一个文件”的归档（data/digests/YYYY-MM-DD.json）。"""
import json
import os
import tempfile
from pathlib import Path

from newsgen import settings

DIGEST_DIR = settings.ROOT / "data" / "digests"


def atomic_json(path, value):
    """先写临时文件并落盘，再原子替换目标文件；写入失败时原文件保持不变。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def digest_path(date):
    """某一期的存档路径。"""
    return DIGEST_DIR / f"{date}.json"


def issue_exists(date):
    """该期是否已有存档。"""
    return digest_path(date).exists()


def save_record(record):
    """一期一个文件：每天的运行只写当天的文件，不会重写整个归档，git 差异也只涉及当期。"""
    atomic_json(digest_path(record["date"]), record)


def archived_dates():
    """已归档的全部期号，升序。"""
    return sorted(path.stem for path in DIGEST_DIR.glob("????-??-??.json"))
