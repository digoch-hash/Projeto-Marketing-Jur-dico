"""Backup em um clique: banco (copia consistente) + logo e fotos enviados, num .zip."""
from __future__ import annotations

import io
import sqlite3
import tempfile
import zipfile
from datetime import date
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import Settings


class BackupError(RuntimeError):
    pass


def sqlite_path(database_url: str) -> Path | None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def make_backup(settings: Settings) -> tuple[bytes, str]:
    db_path = sqlite_path(settings.database_url)
    if db_path is None or not db_path.is_file():
        raise BackupError("O backup pela tela só funciona com o banco SQLite padrão.")
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        copy = Path(tmp) / "app.db"
        src, dst = sqlite3.connect(db_path), sqlite3.connect(copy)
        try:
            src.backup(dst)  # copia consistente mesmo com o sistema em uso
        finally:
            src.close()
            dst.close()
        z.write(copy, "app.db")
        root = Path(settings.data_dir)
        for folder in ("brand", "photos"):
            base = root / folder
            if base.exists():
                for f in base.rglob("*"):
                    if f.is_file() and "thumbs" not in f.parts:
                        z.write(f, f"{folder}/{f.relative_to(base)}")
    return buf.getvalue(), f"backup-hrbio-radar-{date.today().isoformat()}.zip"
