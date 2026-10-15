"""Atomic JSON writes and the one-file-per-issue archive."""
import json
import os
import tempfile
from pathlib import Path

from newsgen import settings

DIGEST_DIR = settings.ROOT / "data" / "digests"


def atomic_json(path, value):
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
    return DIGEST_DIR / f"{date}.json"


def issue_exists(date):
    return digest_path(date).exists()


def save_record(record):
    """One file per issue: a daily run touches only its own date and never rewrites the archive."""
    atomic_json(digest_path(record["date"]), record)
