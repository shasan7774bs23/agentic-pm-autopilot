"""The system's memory: one SQLite file holding ticket analyses, the work list,
daily snapshots for forecasting, and an append-only audit log.

Claude Code starts every session with no memory; everything it must remember lives here.
"""
import datetime as dt
import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    key           TEXT PRIMARY KEY,
    content_hash  TEXT,            -- hash of description + client comments at last analysis
    analysed_at   TEXT,
    verdict       TEXT,            -- READY | NEEDS CLARIFICATION | BLOCKED
    analysis_file TEXT,
    posted_comment_id TEXT
);
CREATE TABLE IF NOT EXISTS work_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,      -- ticket | meeting
    ref        TEXT NOT NULL,      -- ticket key, or transcript id
    reason     TEXT,               -- new | changed | meeting
    fingerprint TEXT NOT NULL,     -- content hash / transcript id: one open item per fingerprint
    status     TEXT NOT NULL DEFAULT 'open',   -- open | done | skipped
    created_at TEXT NOT NULL,
    closed_at  TEXT,
    note       TEXT,
    UNIQUE (kind, ref, fingerprint)
);
CREATE TABLE IF NOT EXISTS snapshots (
    day      TEXT NOT NULL,        -- YYYY-MM-DD, team local time
    scope    TEXT NOT NULL,        -- project key, sprint id, or version name
    metrics  TEXT NOT NULL,        -- JSON
    PRIMARY KEY (day, scope)
);
CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    actor   TEXT NOT NULL,         -- batch | claude | pm
    action  TEXT NOT NULL,         -- e.g. analyse, post_comment, fetch_mirror
    ref     TEXT,                  -- ticket key / repo / transcript
    outcome TEXT NOT NULL,         -- ok | failed | dry_run | skipped
    detail  TEXT                   -- JSON: why, sizes, error text
);
"""


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    @staticmethod
    def from_config(cfg):
        return Store(os.path.join(cfg["analyses_dir"], "pm.sqlite"))

    # ---------- audit: append-only, every outward or failed action lands here ----------
    def audit(self, actor, action, ref, outcome, **detail):
        with self.db:
            self.db.execute("INSERT INTO audit (ts, actor, action, ref, outcome, detail) VALUES (?,?,?,?,?,?)",
                            (_now(), actor, action, ref, outcome, json.dumps(detail, default=str)))

    def audit_rows(self, since=None):
        q, args = "SELECT * FROM audit", ()
        if since:
            q, args = q + " WHERE ts >= ?", (since,)
        return [dict(r) for r in self.db.execute(q + " ORDER BY id", args)]

    # ---------- tickets ----------
    def ticket(self, key):
        r = self.db.execute("SELECT * FROM tickets WHERE key = ?", (key,)).fetchone()
        return dict(r) if r else None

    def mark_analysed(self, key, content_hash, verdict=None, analysis_file=None):
        with self.db:
            self.db.execute(
                "INSERT INTO tickets (key, content_hash, analysed_at, verdict, analysis_file) VALUES (?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET content_hash=excluded.content_hash, analysed_at=excluded.analysed_at, "
                "verdict=COALESCE(excluded.verdict, tickets.verdict), "
                "analysis_file=COALESCE(excluded.analysis_file, tickets.analysis_file)",
                (key, content_hash, _now(), verdict, analysis_file))
            self.db.execute("UPDATE work_items SET status='done', closed_at=? WHERE kind='ticket' AND ref=? "
                            "AND status='open'", (_now(), key))

    def record_post(self, key, comment_id):
        with self.db:
            self.db.execute("UPDATE tickets SET posted_comment_id=? WHERE key=?", (comment_id, key))

    # ---------- work list ----------
    def add_work(self, kind, ref, fingerprint, reason, note=None):
        """Adds an item unless the same content is already listed. Returns True if added."""
        with self.db:
            cur = self.db.execute("INSERT OR IGNORE INTO work_items (kind, ref, reason, fingerprint, created_at, note) "
                                  "VALUES (?,?,?,?,?,?)", (kind, ref, reason, fingerprint, _now(), note))
            if cur.rowcount and kind == "ticket":
                # A newer change supersedes an older open item for the same ticket.
                self.db.execute("UPDATE work_items SET status='skipped', closed_at=?, note='superseded' "
                                "WHERE kind='ticket' AND ref=? AND status='open' AND fingerprint<>?",
                                (_now(), ref, fingerprint))
            return bool(cur.rowcount)

    def open_work(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM work_items WHERE status='open' ORDER BY id")]

    def close_work(self, item_id, status="done", note=None):
        with self.db:
            self.db.execute("UPDATE work_items SET status=?, closed_at=?, note=COALESCE(?, note) WHERE id=?",
                            (status, _now(), note, item_id))

    # ---------- snapshots: the history forecasting needs ----------
    def snapshot(self, day, scope, metrics):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO snapshots (day, scope, metrics) VALUES (?,?,?)",
                            (day, scope, json.dumps(metrics, default=str)))

    def history(self, scope):
        return [(r["day"], json.loads(r["metrics"]))
                for r in self.db.execute("SELECT * FROM snapshots WHERE scope=? ORDER BY day", (scope,))]

    # ---------- one-off import of the old state.json ----------
    def import_state_json(self, path):
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        n = 0
        for key, s in state.items():
            if not self.ticket(key):
                with self.db:
                    self.db.execute("INSERT INTO tickets (key, content_hash, analysed_at, analysis_file) VALUES (?,?,?,?)",
                                    (key, s.get("hash"), s.get("analysed"), s.get("file") or None))
                n += 1
        return n
