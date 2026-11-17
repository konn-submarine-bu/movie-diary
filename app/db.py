from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import AppError

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
CREATE TABLE IF NOT EXISTS impressions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('one', 'feel', 'rate', 'idea', 'word', 'mine')),
  short TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  stars INTEGER CHECK (stars IS NULL OR stars BETWEEN 1 AND 5),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ok', 'edit', 'rej')),
  source_msg_ids TEXT NOT NULL DEFAULT '[]',
  pos_x REAL,
  pos_y REAL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_impressions_entry ON impressions(entry_id, id);
CREATE TABLE IF NOT EXISTS impression_state (
  entry_id INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
  last_msg_id INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS film_info (
  tmdb_id INTEGER PRIMARY KEY,
  data TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
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


def _impression(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["source_msg_ids"] = json.loads(d["source_msg_ids"])
    return d


ACTIVE = ("ok", "edit")
IMPRESSION_PATCH_FIELDS = ("short", "text", "stars", "status", "pos_x", "pos_y")


def _written(imp: dict[str, Any]) -> Any:
    """The value an adopted impression pushes into its diary entry (note for `one`, rating for `rate`)."""
    return (imp["text"] or imp["short"]) if imp["kind"] == "one" else imp["stars"]


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

    # impressions -------------------------------------------------------
    def _sync_entry(self, conn: sqlite3.Connection, before: dict[str, Any], after: dict[str, Any]) -> None:
        """Keep entries.note / entries.rating equal to the adopted `one` / `rate` impression.

        Adopting one makes it the only adopted impression of its kind. Dropping it clears the entry
        field only if the field still holds what the impression wrote, so a note typed by hand survives.
        """
        kind = after["kind"]
        if kind not in ("one", "rate"):
            return
        col = "note" if kind == "one" else "rating"
        stamp = now_iso()
        if after["status"] in ACTIVE:
            conn.execute("UPDATE impressions SET status = 'rej', updated_at = ? WHERE entry_id = ? AND kind = ?"
                         " AND id != ? AND status IN ('ok', 'edit')",
                         (stamp, after["entry_id"], kind, after["id"]))
            conn.execute(f"UPDATE entries SET {col} = ?, updated_at = ? WHERE id = ?",
                         (_written(after), stamp, after["entry_id"]))
        elif before["status"] in ACTIVE:
            row = conn.execute(f"SELECT {col} FROM entries WHERE id = ?", (after["entry_id"],)).fetchone()
            if row is not None and row[col] == _written(before):
                conn.execute(f"UPDATE entries SET {col} = ?, updated_at = ? WHERE id = ?",
                             ("" if kind == "one" else None, stamp, after["entry_id"]))

    def list_impressions(self, entry_id: int) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute("SELECT * FROM impressions WHERE entry_id = ? ORDER BY id", (entry_id,)).fetchall()
        return [_impression(r) for r in rows]

    def get_impression(self, imp_id: int) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM impressions WHERE id = ?", (imp_id,)).fetchone()
        return _impression(row) if row else None

    def add_impression(self, entry_id: int, kind: str, short: str, text: str = "", stars: int | None = None,
                       status: str = "draft", source_msg_ids: list[int] | None = None,
                       pos: tuple[float, float] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if kind == "rate" and status in ACTIVE and not stars:
            raise AppError("impression_stars_required", "an adopted rating bubble needs stars from 1 to 5", 422)
        stamp = now_iso()
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO impressions (entry_id, kind, short, text, stars, status, source_msg_ids, pos_x, pos_y,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (entry_id, kind, short, text, stars if kind == "rate" else None, status,
                 json.dumps(source_msg_ids or []), pos[0] if pos else None, pos[1] if pos else None, stamp, stamp))
            after = _impression(conn.execute("SELECT * FROM impressions WHERE id = ?", (cur.lastrowid,)).fetchone())
            self._sync_entry(conn, {**after, "status": "rej"}, after)
            after = _impression(conn.execute("SELECT * FROM impressions WHERE id = ?", (cur.lastrowid,)).fetchone())
            entry = _entry(conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone())
        return after, entry

    def update_impression(self, imp_id: int, patch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
        values = {k: v for k, v in patch.items() if k in IMPRESSION_PATCH_FIELDS}
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM impressions WHERE id = ?", (imp_id,)).fetchone()
            if row is None:
                return None
            before = _impression(row)
            after = {**before, **values}
            edited = any(k in values and values[k] != before[k] for k in ("short", "text", "stars"))
            if edited and "status" not in values and before["status"] in ("draft", "ok"):
                after["status"] = "edit"
            if after["kind"] != "rate":
                after["stars"] = None
            if after["kind"] == "rate" and after["status"] in ACTIVE and not after["stars"]:
                raise AppError("impression_stars_required", "an adopted rating bubble needs stars from 1 to 5", 422)
            conn.execute("UPDATE impressions SET short = ?, text = ?, stars = ?, status = ?, pos_x = ?, pos_y = ?,"
                         " updated_at = ? WHERE id = ?",
                         (after["short"], after["text"], after["stars"], after["status"],
                          after["pos_x"], after["pos_y"], now_iso(), imp_id))
            self._sync_entry(conn, before, after)
            after = _impression(conn.execute("SELECT * FROM impressions WHERE id = ?", (imp_id,)).fetchone())
            entry = _entry(conn.execute("SELECT * FROM entries WHERE id = ?", (before["entry_id"],)).fetchone())
        return after, entry

    def revise_impression(self, imp_id: int, short: str, text: str, stars: int | None,
                          source_msg_ids: list[int]) -> bool:
        """Model-side rewrite. Only touches drafts, so adopted or calibrated bubbles can never be overwritten."""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM impressions WHERE id = ? AND status = 'draft'", (imp_id,)).fetchone()
            if row is None:
                return False
            merged = sorted({*json.loads(row["source_msg_ids"]), *source_msg_ids})
            conn.execute("UPDATE impressions SET short = ?, text = ?, stars = ?, source_msg_ids = ?, updated_at = ?"
                         " WHERE id = ?",
                         (short, text, stars if row["kind"] == "rate" else None, json.dumps(merged), now_iso(), imp_id))
        return True

    def seed_impressions(self, entry: dict[str, Any]) -> bool:
        """First visit to an entry that already has a note or rating: show them as calibrated bubbles, once."""
        stamp = now_iso()
        with self._tx() as conn:
            if conn.execute("INSERT OR IGNORE INTO impression_state (entry_id, last_msg_id) VALUES (?, 0)",
                            (entry["id"],)).rowcount == 0:
                return False
            if entry["note"]:
                note = entry["note"]
                conn.execute("INSERT INTO impressions (entry_id, kind, short, text, status, created_at, updated_at)"
                             " VALUES (?, 'one', ?, ?, 'edit', ?, ?)",
                             (entry["id"], note if len(note) <= 40 else note[:39] + "…", note, stamp, stamp))
            if entry["rating"]:
                conn.execute("INSERT INTO impressions (entry_id, kind, short, stars, status, created_at, updated_at)"
                             " VALUES (?, 'rate', '我的评分', ?, 'edit', ?, ?)",
                             (entry["id"], entry["rating"], stamp, stamp))
        return True

    def delete_impression(self, imp_id: int) -> dict[str, Any] | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM impressions WHERE id = ?", (imp_id,)).fetchone()
            if row is None:
                return None
            before = _impression(row)
            conn.execute("DELETE FROM impressions WHERE id = ?", (imp_id,))
            self._sync_entry(conn, before, {**before, "status": "rej"})
            return _entry(conn.execute("SELECT * FROM entries WHERE id = ?", (before["entry_id"],)).fetchone())

    def last_summarized(self, entry_id: int) -> int:
        with self._tx() as conn:
            row = conn.execute("SELECT last_msg_id FROM impression_state WHERE entry_id = ?", (entry_id,)).fetchone()
        return row["last_msg_id"] if row else 0

    def set_last_summarized(self, entry_id: int, msg_id: int) -> None:
        with self._tx() as conn:
            conn.execute("INSERT INTO impression_state (entry_id, last_msg_id) VALUES (?, ?)"
                         " ON CONFLICT(entry_id) DO UPDATE SET last_msg_id = excluded.last_msg_id",
                         (entry_id, msg_id))

    # film info cache ---------------------------------------------------
    def get_film_info(self, tmdb_id: int) -> tuple[dict[str, Any], str] | None:
        with self._tx() as conn:
            row = conn.execute("SELECT data, fetched_at FROM film_info WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
        return (json.loads(row["data"]), row["fetched_at"]) if row else None

    def put_film_info(self, tmdb_id: int, data: dict[str, Any]) -> None:
        with self._tx() as conn:
            conn.execute("INSERT INTO film_info (tmdb_id, data, fetched_at) VALUES (?, ?, ?)"
                         " ON CONFLICT(tmdb_id) DO UPDATE SET data = excluded.data, fetched_at = excluded.fetched_at",
                         (tmdb_id, json.dumps(data, ensure_ascii=False), now_iso()))
