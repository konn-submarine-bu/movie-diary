from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tmdb_id INTEGER,
  title TEXT NOT NULL,
  original_title TEXT NOT NULL DEFAULT '',
  year INTEGER,
  director TEXT NOT NULL DEFAULT '',
  overview TEXT NOT NULL DEFAULT '',
  poster_url TEXT NOT NULL DEFAULT '',
  genres TEXT NOT NULL DEFAULT '[]',
  rating INTEGER,
  note TEXT NOT NULL DEFAULT '',
  tags TEXT NOT NULL DEFAULT '[]',
  watched_on TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'watched' CHECK (status IN ('watched', 'wishlist')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_tmdb ON entries(tmdb_id) WHERE tmdb_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id INTEGER REFERENCES entries(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  meta TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(entry_id, id);
CREATE TABLE IF NOT EXISTS posters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  quote TEXT NOT NULL,
  mood TEXT NOT NULL DEFAULT '',
  image_prompt TEXT NOT NULL DEFAULT '',
  filename TEXT NOT NULL,
  fallback_code TEXT NOT NULL DEFAULT '',
  fallback_message TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posters_entry ON posters(entry_id, id);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('quote', 'shot')),
  text TEXT NOT NULL DEFAULT '',
  filename TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clips_entry ON clips(entry_id, id);
CREATE TABLE IF NOT EXISTS poems (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  style_key TEXT NOT NULL,
  style_name TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_poems_entry ON poems(entry_id, id);
"""

ENTRY_FIELDS = (
    "tmdb_id", "title", "original_title", "year", "director", "overview",
    "poster_url", "genres", "rating", "note", "tags", "watched_on", "status",
)
JSON_ENTRY_FIELDS = ("genres", "tags")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _entry(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in JSON_ENTRY_FIELDS:
        d[key] = json.loads(d[key])
    return d


def _message(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["meta"] = json.loads(d["meta"])
    return d


def _clip(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["url"] = f"/media/clips/{d['filename']}" if d["filename"] else ""
    return d


def _poster(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["url"] = f"/media/posters/{d['filename']}"
    return d


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as conn:
            conn.executescript(SCHEMA)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)")}
            if "status" not in cols:  # databases created before the wishlist existed
                conn.execute("ALTER TABLE entries ADD COLUMN status TEXT NOT NULL DEFAULT 'watched'"
                             " CHECK (status IN ('watched', 'wishlist'))")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    # entries -----------------------------------------------------------
    def create_entry(self, data: dict[str, Any]) -> dict[str, Any]:
        values = {k: data[k] for k in ENTRY_FIELDS if k in data}
        for key in JSON_ENTRY_FIELDS:
            if key in values:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        stamp = now_iso()
        values["created_at"] = values["updated_at"] = stamp
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self._tx() as conn:
            cur = conn.execute(f"INSERT INTO entries ({cols}) VALUES ({marks})", list(values.values()))
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _entry(row)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return _entry(row) if row else None

    def list_entries(self, limit: int = 500, min_rating: int | None = None,
                     status: str | None = "watched") -> list[dict[str, Any]]:
        """status=None returns every entry; the default keeps wishlist items out of diary views."""
        where: list[str] = []
        params: list[Any] = []
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if min_rating is not None:
            where.append("rating >= ?")
            params.append(min_rating)
        sql = "SELECT * FROM entries"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY watched_on DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._tx() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_entry(r) for r in rows]

    def find_duplicate(self, title: str, year: int | None, tmdb_id: int | None) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = None
            if tmdb_id is not None:
                row = conn.execute("SELECT * FROM entries WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM entries WHERE title = ? AND year IS ?", (title, year)
                ).fetchone()
        return _entry(row) if row else None

    def update_entry(self, entry_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
        values = {k: v for k, v in patch.items() if k in ENTRY_FIELDS}
        for key in JSON_ENTRY_FIELDS:
            if key in values:
                values[key] = json.dumps(values[key], ensure_ascii=False)
        values["updated_at"] = now_iso()
        with self._tx() as conn:
            if values.get("status") == "watched" and "watched_on" not in values:
                row = conn.execute("SELECT watched_on FROM entries WHERE id = ?", (entry_id,)).fetchone()
                if row is not None and not row["watched_on"]:
                    values["watched_on"] = date.today().isoformat()
            sets = ", ".join(f"{k} = ?" for k in values)
            cur = conn.execute(f"UPDATE entries SET {sets} WHERE id = ?", [*values.values(), entry_id])
            if cur.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return _entry(row)

    def delete_entry(self, entry_id: int) -> dict[str, list[str]]:
        """Delete an entry and return the poster and clip image filenames that belonged to it."""
        with self._tx() as conn:
            posters = [r["filename"] for r in conn.execute(
                "SELECT filename FROM posters WHERE entry_id = ?", (entry_id,))]
            clips = [r["filename"] for r in conn.execute(
                "SELECT filename FROM clips WHERE entry_id = ? AND filename != ''", (entry_id,))]
            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        return {"posters": posters, "clips": clips}

    # clips (quotes and screenshots) -----------------------------------------
    def add_clip(self, entry_id: int, kind: str, text: str = "", note: str = "",
                 filename: str = "") -> dict[str, Any]:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO clips (entry_id, kind, text, filename, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (entry_id, kind, text, filename, note, now_iso()))
            row = conn.execute("SELECT * FROM clips WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _clip(row)

    def get_clip(self, clip_id: int) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return _clip(row) if row else None

    def find_clip(self, entry_id: int, kind: str, text: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM clips WHERE entry_id = ? AND kind = ? AND text = ?",
                               (entry_id, kind, text)).fetchone()
        return _clip(row) if row else None

    def list_clips(self, entry_id: int | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        where, params = [], []
        if entry_id is not None:
            where.append("entry_id = ?")
            params.append(entry_id)
        if kind is not None:
            where.append("kind = ?")
            params.append(kind)
        sql = "SELECT * FROM clips" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
        with self._tx() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_clip(r) for r in rows]

    # poems -------------------------------------------------------------
    def add_poem(self, entry_id: int, style_key: str, style_name: str, title: str, body: str) -> dict[str, Any]:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO poems (entry_id, style_key, style_name, title, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (entry_id, style_key, style_name, title, body, now_iso()))
            row = conn.execute("SELECT * FROM poems WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def find_poem(self, entry_id: int, body: str) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM poems WHERE entry_id = ? AND body = ?", (entry_id, body)).fetchone()
        return dict(row) if row else None

    def get_poem(self, poem_id: int) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM poems WHERE id = ?", (poem_id,)).fetchone()
        return dict(row) if row else None

    def list_poems(self, entry_id: int | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM poems", []
        if entry_id is not None:
            sql += " WHERE entry_id = ?"
            params.append(entry_id)
        with self._tx() as conn:
            rows = conn.execute(sql + " ORDER BY id DESC", params).fetchall()
        return [dict(r) for r in rows]

    def delete_poem(self, poem_id: int) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM poems WHERE id = ?", (poem_id,))

    def delete_clip(self, clip_id: int) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))

    # messages ----------------------------------------------------------
    def add_message(self, entry_id: int | None, role: str, content: str,
                    meta: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO messages (entry_id, role, content, meta, created_at) VALUES (?, ?, ?, ?, ?)",
                (entry_id, role, content, json.dumps(meta or {}, ensure_ascii=False), now_iso()),
            )
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _message(row)

    def list_messages(self, entry_id: int | None, limit: int = 200) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM (SELECT * FROM messages WHERE entry_id IS ? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (entry_id, limit),
            ).fetchall()
        return [_message(r) for r in rows]

    def clear_messages(self, entry_id: int | None) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM messages WHERE entry_id IS ?", (entry_id,))

    # posters -----------------------------------------------------------
    def add_poster(self, entry_id: int, quote: str, mood: str, image_prompt: str, filename: str,
                   fallback_code: str = "", fallback_message: str = "") -> dict[str, Any]:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO posters (entry_id, quote, mood, image_prompt, filename, fallback_code,"
                " fallback_message, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, quote, mood, image_prompt, filename, fallback_code, fallback_message, now_iso()),
            )
            row = conn.execute("SELECT * FROM posters WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _poster(row)

    def get_poster(self, poster_id: int) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM posters WHERE id = ?", (poster_id,)).fetchone()
        return _poster(row) if row else None

    def list_posters(self, entry_id: int) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM posters WHERE entry_id = ? ORDER BY id DESC", (entry_id,)).fetchall()
        return [_poster(r) for r in rows]

    def delete_poster(self, poster_id: int) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM posters WHERE id = ?", (poster_id,))
