"""SQLite-backed index of corpus files.

The index stores one row per file in `data_dir`. It does NOT store file
contents — only metadata + a sha256 for change detection. Tool results
(extracted text, frames, descriptions) live in separate cache tables or
on disk under `cache_dir`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    return "other"


@dataclass
class CorpusItem:
    path: str  # relative to data_dir
    kind: str  # video | pdf | image | other
    size_bytes: int
    sha256: str
    mtime: float
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    page_count: int | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    path        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    mtime       REAL NOT NULL,
    duration_s  REAL,
    width       INTEGER,
    height      INTEGER,
    page_count  INTEGER,
    indexed_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);

CREATE TABLE IF NOT EXISTS analysis_cache (
    item_path   TEXT NOT NULL,
    tool        TEXT NOT NULL,
    mode        TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (item_path, tool, mode, params_hash)
);

-- Full-text search index over extracted PDF text. Populated lazily by
-- analyze_pdf_text / analyze_pdf_ocr.
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    path UNINDEXED,
    text,
    tokenize='porter unicode61'
);
"""


class Corpus:
    def __init__(self, data_dir: Path, cache_dir: Path):
        self.data_dir = data_dir
        self.cache_dir = cache_dir
        self.db_path = cache_dir / "index.db"
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- discovery ----------------------------------------------------------

    def scan(self, *, deep_hash: bool = False) -> dict[str, int]:
        """Walk data_dir and upsert each file's metadata.

        Returns a small summary dict. Hashing is light (first+last 1 MB) by
        default; pass deep_hash=True to hash the whole file (slow on big PDFs).
        """
        seen = 0
        added = 0
        updated = 0
        skipped = 0
        now = time.time()

        with self._conn() as c:
            for p in self.data_dir.rglob("*"):
                if not p.is_file():
                    continue
                if any(part.startswith(".") for part in p.relative_to(self.data_dir).parts):
                    skipped += 1
                    continue
                if p.name.startswith("._"):
                    skipped += 1
                    continue
                seen += 1
                rel = str(p.relative_to(self.data_dir))
                st = p.stat()

                row = c.execute(
                    "SELECT mtime, size_bytes FROM items WHERE path = ?", (rel,)
                ).fetchone()

                if row and abs(row["mtime"] - st.st_mtime) < 1 and row["size_bytes"] == st.st_size:
                    continue  # unchanged

                kind = classify(p)
                digest = _hash_file(p, full=deep_hash)
                item = CorpusItem(
                    path=rel,
                    kind=kind,
                    size_bytes=st.st_size,
                    sha256=digest,
                    mtime=st.st_mtime,
                )
                c.execute(
                    """
                    INSERT INTO items (path, kind, size_bytes, sha256, mtime, indexed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        kind = excluded.kind,
                        size_bytes = excluded.size_bytes,
                        sha256 = excluded.sha256,
                        mtime = excluded.mtime,
                        indexed_at = excluded.indexed_at
                    """,
                    (item.path, item.kind, item.size_bytes, item.sha256, item.mtime, now),
                )
                if row is None:
                    added += 1
                else:
                    updated += 1

        return {"seen": seen, "added": added, "updated": updated, "skipped": skipped}

    # ---- queries ------------------------------------------------------------

    def list(self, kind: str | None = None, filter_substr: str | None = None) -> list[dict]:
        sql = "SELECT * FROM items"
        clauses = []
        args: list = []
        if kind:
            clauses.append("kind = ?")
            args.append(kind)
        if filter_substr:
            clauses.append("path LIKE ?")
            args.append(f"%{filter_substr}%")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY path"

        with self._conn() as c:
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def get(self, rel_path: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM items WHERE path = ?", (rel_path,)).fetchone()
        return dict(row) if row else None

    def update_video_meta(
        self, rel_path: str, duration_s: float, width: int, height: int
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE items SET duration_s = ?, width = ?, height = ? WHERE path = ?",
                (duration_s, width, height, rel_path),
            )

    def update_pdf_meta(self, rel_path: str, page_count: int) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE items SET page_count = ? WHERE path = ?",
                (page_count, rel_path),
            )

    # ---- analysis cache -----------------------------------------------------

    def get_cached(
        self, item_path: str, tool: str, mode: str, params_hash: str
    ) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                """
                SELECT result_json FROM analysis_cache
                WHERE item_path = ? AND tool = ? AND mode = ? AND params_hash = ?
                """,
                (item_path, tool, mode, params_hash),
            ).fetchone()
        if row:
            return json.loads(row["result_json"])
        return None

    def put_cached(
        self,
        item_path: str,
        tool: str,
        mode: str,
        params_hash: str,
        result: dict,
    ) -> None:
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO analysis_cache
                  (item_path, tool, mode, params_hash, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_path, tool, mode, params_hash) DO UPDATE SET
                    result_json = excluded.result_json,
                    created_at = excluded.created_at
                """,
                (item_path, tool, mode, params_hash, json.dumps(result), time.time()),
            )

    # ---- full-text search (PDFs) -------------------------------------------

    def fts_upsert(self, rel_path: str, text: str) -> None:
        """Insert or replace the FTS row for this path."""
        with self._conn() as c:
            c.execute("DELETE FROM fts WHERE path = ?", (rel_path,))
            c.execute("INSERT INTO fts (path, text) VALUES (?, ?)", (rel_path, text))

    def fts_search(
        self, query: str, *, limit: int = 10, kind: str | None = None
    ) -> list[dict]:
        """Run an FTS5 query. Returns hits ordered by bm25 ranking.

        `query` accepts FTS5 syntax (AND / OR / NOT / "phrase" / col:term).
        """
        with self._conn() as c:
            # Join with items so we can filter by kind and surface metadata.
            sql = """
                SELECT
                    fts.path AS path,
                    snippet(fts, 1, '«', '»', '…', 16) AS snippet,
                    bm25(fts) AS score,
                    items.kind AS kind,
                    items.page_count AS page_count,
                    items.size_bytes AS size_bytes
                FROM fts
                JOIN items ON items.path = fts.path
                WHERE fts MATCH ?
            """
            args: list = [query]
            if kind:
                sql += " AND items.kind = ?"
                args.append(kind)
            sql += " ORDER BY score LIMIT ?"
            args.append(limit)
            rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def fts_indexed_count(self) -> int:
        with self._conn() as c:
            row = c.execute("SELECT count(*) AS n FROM fts").fetchone()
        return int(row["n"]) if row else 0


def _hash_file(path: Path, *, full: bool = False, chunk_size: int = 1 << 20) -> str:
    """Fast-ish hash of a file: first+last chunk by default, or full content."""
    h = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as f:
        if full:
            while chunk := f.read(chunk_size):
                h.update(chunk)
        else:
            h.update(f.read(chunk_size))
            if size > chunk_size:
                f.seek(max(0, size - chunk_size))
                h.update(f.read(chunk_size))
    return h.hexdigest()


def serialize_item(d: dict) -> dict:
    """Strip internal fields for tool output."""
    return {
        "path": d.get("path"),
        "kind": d.get("kind"),
        "size_bytes": d.get("size_bytes"),
        "duration_s": d.get("duration_s"),
        "width": d.get("width"),
        "height": d.get("height"),
        "page_count": d.get("page_count"),
    }
