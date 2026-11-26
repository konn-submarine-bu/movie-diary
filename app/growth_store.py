from __future__ import annotations

import sqlite3
import time
from typing import Any

from .errors import AppError

SCHEMA = """
CREATE TABLE IF NOT EXISTS growth_state (
    id INTEGER PRIMARY KEY CHECK (id = 1), epoch INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT ''
);
INSERT OR IGNORE INTO growth_state (id) VALUES (1);
CREATE TABLE IF NOT EXISTS growth_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('piece', 'chat')),
    source_id INTEGER NOT NULL, version INTEGER NOT NULL,
    ticket INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0, ready_at REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, source_id, version)
);
CREATE INDEX IF NOT EXISTS growth_jobs_ready ON growth_jobs (status, ready_at, id);
CREATE TABLE IF NOT EXISTS mind_topics (
    id INTEGER PRIMARY KEY, label TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS mind_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('social', 'preference', 'psychology', 'user')),
    topic_id INTEGER REFERENCES mind_topics(id), text TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('active', 'withdrawn', 'superseded', 'invalid')),
    uncertain INTEGER NOT NULL DEFAULT 0,
    replaces INTEGER REFERENCES mind_nodes(id)
);
CREATE INDEX IF NOT EXISTS mind_nodes_active ON mind_nodes (status, topic_id, id);
CREATE TABLE IF NOT EXISTS mind_evidence (
    node_id INTEGER NOT NULL REFERENCES mind_nodes(id),
    kind TEXT NOT NULL, source_id INTEGER NOT NULL, version INTEGER NOT NULL,
    field TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('supports', 'counter', 'context', 'confirmed')),
    PRIMARY KEY (node_id, kind, source_id, version, field, start, end, relation)
);
CREATE INDEX IF NOT EXISTS mind_evidence_source ON mind_evidence (kind, source_id, version);
"""


def enqueue(conn: sqlite3.Connection, kind: str, source_id: int, version: int, *, recompute: bool = False) -> None:
    sql = "INSERT INTO growth_jobs (kind, source_id, version) VALUES (?, ?, ?) ON CONFLICT(kind, source_id, version)"
    sql += (" DO UPDATE SET status='pending', attempts=0, ready_at=0, error='', ticket=ticket+1"
            if recompute else " DO NOTHING")
    conn.execute(sql, (kind, source_id, version))


def invalidate(conn: sqlite3.Connection, kind: str, source_id: int | None = None) -> None:
    """Stop serving all dependent judgments before rebuilding from any surviving sources."""
    clause, args = ("kind=?", (kind,)) if source_id is None else ("kind=? AND source_id=?", (kind, source_id))
    targets = f"SELECT node_id FROM mind_evidence WHERE {clause}"
    outside = "e.kind=?" if source_id is None else "e.kind=? AND e.source_id=?"
    survivors = conn.execute(
        "SELECT DISTINCT e.kind, e.source_id, e.version FROM mind_evidence e"
        " JOIN mind_nodes n ON e.node_id=n.id WHERE n.status IN ('active', 'withdrawn')"
        f" AND n.kind != 'user' AND e.node_id IN ({targets}) AND NOT ({outside})",
        (*args, *args)).fetchall()
    conn.execute(f"UPDATE mind_nodes SET status='invalid', text='', reason='', topic_id=NULL"
                 f" WHERE id IN ({targets})", args)
    conn.execute("DELETE FROM mind_evidence WHERE node_id IN (SELECT id FROM mind_nodes WHERE status='invalid')")
    conn.execute("DELETE FROM mind_topics WHERE NOT EXISTS (SELECT 1 FROM mind_nodes WHERE topic_id=mind_topics.id)")
    conn.execute(f"DELETE FROM growth_jobs WHERE {clause}", args)
    conn.execute("UPDATE growth_state SET epoch=epoch+1 WHERE id=1")
    for survivor in survivors:
        enqueue(conn, *survivor, recompute=True)


def epoch(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT epoch FROM growth_state WHERE id=1").fetchone()[0]


def _insert_node(conn: sqlite3.Connection, change: dict[str, Any]) -> int:
    topic = change['topic']
    conn.execute("INSERT OR IGNORE INTO mind_topics (label) VALUES (?)", (topic,))
    cur = conn.execute(
        "INSERT INTO mind_nodes (kind, topic_id, text, reason, status, uncertain, replaces)"
        " VALUES (?, (SELECT id FROM mind_topics WHERE label=?), ?, ?, ?, ?, ?)",
        (change['kind'], topic, change['text'], change.get('reason', ''), change.get('status', 'active'),
         bool(change.get('uncertain')), change.get('replaces')))
    node_id = cur.lastrowid
    if change.get('replaces'):
        conn.execute("UPDATE mind_nodes SET status='superseded' WHERE id=?", (change['replaces'],))
        conn.execute("INSERT INTO mind_evidence SELECT ?, kind, source_id, version, field, start, end, relation"
                     " FROM mind_evidence WHERE node_id=?", (node_id, change['replaces']))
    conn.executemany(
        "INSERT OR IGNORE INTO mind_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(node_id, e['kind'], e['source_id'], e['version'], e['field'], e['start'], e['end'], e['relation'])
         for e in change['evidence']])
    return node_id


class GrowthStore:
    def __init__(self, transaction):
        self._tx = transaction

    def current_epoch(self) -> int:
        with self._tx() as conn:
            return epoch(conn)

    def status(self) -> dict[str, Any]:
        with self._tx() as conn:
            conn.execute("BEGIN")
            counts = conn.execute("SELECT status, count(*) AS n FROM growth_jobs"
                                  " WHERE status!='done' GROUP BY status").fetchall()
            pending = sum(row['n'] for row in counts)
            retryable = sum(row['n'] for row in counts if row['status'] == 'pending')
            error = conn.execute("SELECT error FROM growth_jobs WHERE error!='' ORDER BY id DESC LIMIT 1").fetchone()
            row = conn.execute("SELECT updated_at, (SELECT count(*) FROM pieces) AS total FROM growth_state").fetchone()
        return {**dict(row), 'pending': pending, 'retryable': retryable,
                'error': error[0] if error else ''}

    def jobs(self, limit: int) -> tuple[int, list[dict[str, Any]]]:
        with self._tx() as conn:
            conn.execute("BEGIN")
            rows = conn.execute("SELECT * FROM growth_jobs WHERE status='pending' AND ready_at<=?"
                                " ORDER BY id LIMIT ?", (time.time(), limit)).fetchall()
            return epoch(conn), [dict(row) for row in rows]

    def retry(self) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE growth_jobs SET status='pending', attempts=0, ready_at=0, ticket=ticket+1"
                         " WHERE error!=''")

    def finish(self, current_epoch: int, jobs: list[dict[str, Any]], changes: list[dict[str, Any]],
               stamp: str, *, error: str = '') -> bool:
        with self._tx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if epoch(conn) != current_epoch:
                return False
            for job in jobs:
                ok = conn.execute("SELECT 1 FROM growth_jobs WHERE id=? AND ticket=? AND status='pending'",
                                  (job['id'], job['ticket'])).fetchone()
                if not ok:
                    return False
            for change in changes:
                if any(not conn.execute("SELECT 1 FROM mind_nodes WHERE id=? AND status IN ('active', 'superseded')",
                                        (node,)).fetchone() for node in change.get('requires', [])):
                    conn.rollback()
                    return False
                if change.get('replaces'):
                    current = conn.execute("SELECT status FROM mind_nodes WHERE id=?", (change['replaces'],)).fetchone()
                    if not current or current['status'] != 'active':
                        # Another worker revised this node; retry the batch with a fresh graph snapshot.
                        conn.rollback()
                        return False
                else:
                    exact = conn.execute(
                        "SELECT n.id, n.uncertain FROM mind_nodes n JOIN mind_topics t ON t.id=n.topic_id"
                        " WHERE n.status='active' AND n.kind=? AND t.label=? AND n.text=?",
                        (change['kind'], change['topic'], change['text'])).fetchone()
                    if exact:
                        known = {tuple(row) for row in conn.execute(
                            "SELECT kind, source_id, version, field, start, end, relation FROM mind_evidence"
                            " WHERE node_id=?", (exact['id'],))}
                        fields = ('kind', 'source_id', 'version', 'field', 'start', 'end', 'relation')
                        if all(tuple(e[k] for k in fields) in known for e in change['evidence']):
                            continue
                        change = {**change, 'replaces': exact['id'], 'reason': change['reason'] or '补充了新的证据。',
                                  'uncertain': change['uncertain'] or bool(exact['uncertain'])}
                _insert_node(conn, change)
            for job in jobs:
                attempts = job['attempts'] + 1 if error else 0
                status = ('failed' if attempts >= 3 else 'pending') if error else 'done'
                ready_at = time.time() + 5 * 2 ** attempts if error else 0
                conn.execute("UPDATE growth_jobs SET status=?, attempts=?, ready_at=?, error=?, ticket=ticket+1 WHERE id=?",
                             (status, attempts, ready_at, error, job['id']))
            if not error:
                conn.execute("UPDATE growth_state SET updated_at=? WHERE id=1", (stamp,))
        return True

    def nodes(self, node_id: int | None = None, *, query: str | None = None, general: tuple[str, ...] = (),
              topic: str | None = None, include_user: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        args: list[Any] = []
        clauses = ["n.status='active'" if node_id is None else "n.status!='invalid' AND n.id=?"]
        if node_id is not None:
            args.append(node_id)
        if not include_user:
            clauses.append("n.kind!='user'")
        if topic is not None:
            clauses.append("t.label=?")
            args.append(topic)
        order = 'n.id DESC'
        if query is not None:
            topic_match = "length(t.label)>0 AND instr(?, t.label)>0"
            clauses.append('(' + topic_match + (f" OR n.kind IN ({','.join('?' for _ in general)})" if general else '') + ')')
            args.extend((query, *general))
            order = f'({topic_match}) DESC, ' + order
            args.append(query)
        with self._tx() as conn:
            conn.execute("BEGIN")
            rows = conn.execute("SELECT n.*, t.label AS topic FROM mind_nodes n JOIN mind_topics t ON t.id=n.topic_id"
                                f" WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT ?", (*args, limit)).fetchall()
            by_node: dict[int, list[dict[str, Any]]] = {row['id']: [] for row in rows}
            if not rows:
                return []
            ids = list(by_node)
            for offset in range(0, len(ids), 100):
                batch = ids[offset:offset + 100]
                evidence = conn.execute(f"SELECT * FROM mind_evidence WHERE node_id IN ({','.join('?' for _ in batch)})"
                                        " ORDER BY rowid", batch)
                for edge in evidence:
                    item = dict(edge)
                    by_node[item.pop('node_id')].append(item)
        return [{**dict(row), 'uncertain': bool(row['uncertain']), 'evidence': by_node[row['id']]} for row in rows]

    def active_ids(self, ids: set[int]) -> set[int]:
        if not ids:
            return set()
        active = set()
        ordered = sorted(ids)
        with self._tx() as conn:
            conn.execute("BEGIN")
            for offset in range(0, len(ordered), 100):
                batch = ordered[offset:offset + 100]
                matches = conn.execute("SELECT id FROM mind_nodes WHERE status='active'"
                                       f" AND id IN ({','.join('?' for _ in batch)})", batch)
                active.update(row[0] for row in matches)
        return active

    def remember(self, message_id: int, topic: str) -> int:
        with self._tx() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT content FROM piece_messages WHERE id=? AND role='user'", (message_id,)).fetchone()
            if not row or not row['content'].strip():
                raise AppError('mind_memory_source_invalid', '只能记住用户自己的文字消息。', 422)
            if len(row['content']) > 400:
                raise AppError('mind_memory_too_long', '请用不超过 400 字的一条消息表达要记住的内容。', 422)
            existing = conn.execute("SELECT n.id FROM mind_nodes n JOIN mind_evidence e ON n.id=e.node_id"
                                    " WHERE n.kind='user' AND n.status='active' AND e.kind='chat' AND e.source_id=?",
                                    (message_id,)).fetchone()
            if existing:
                return existing['id']
            return _insert_node(conn, {'kind': 'user', 'topic': topic, 'text': row['content'], 'evidence': [{
                'kind': 'chat', 'source_id': message_id, 'version': 1, 'field': 'content',
                'start': 0, 'end': len(row['content']), 'relation': 'confirmed'}]})

    def forget(self, node_id: int) -> bool:
        with self._tx() as conn:
            cur = conn.execute("UPDATE mind_nodes SET status='invalid', text='', reason='', topic_id=NULL"
                               " WHERE id=? AND kind='user' AND status='active'", (node_id,))
            if cur.rowcount:
                conn.execute("DELETE FROM mind_evidence WHERE node_id=?", (node_id,))
                conn.execute("DELETE FROM mind_topics WHERE NOT EXISTS (SELECT 1 FROM mind_nodes WHERE topic_id=mind_topics.id)")
                conn.execute("UPDATE growth_state SET epoch=epoch+1 WHERE id=1")
            return bool(cur.rowcount)
