"""SQLite persistence. Standard library only."""

import sqlite3
import json
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "trawl.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,   -- "<source>:<native_id>"
    source        TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT,
    url           TEXT,
    score         INTEGER DEFAULT 0,
    comments      INTEGER DEFAULT 0,
    created_utc   REAL,
    first_seen    REAL,
    last_seen     REAL,
    rank_score    REAL DEFAULT 0,
    theme         TEXT,
    audience      TEXT,               -- "builder" or "business"
    funnel_asset  TEXT,
    why           TEXT,               -- JSON breakdown, so the UI can show why it ranked
    posted        INTEGER DEFAULT 0   -- you marked it used
);
CREATE INDEX IF NOT EXISTS idx_rank   ON items(rank_score DESC);
CREATE INDEX IF NOT EXISTS idx_posted ON items(posted);
"""


# Columns added after the first release. `CREATE TABLE IF NOT EXISTS` does nothing to an
# existing table, so new columns have to be added explicitly or every fetch dies with
# "table items has no column named X". Dropping the database instead would throw away the
# `posted` flags, which are the one piece of state here that can't be re-fetched.
MIGRATIONS = [
    ("audience", "TEXT"),
    ("funnel_asset", "TEXT"),
    ("why", "TEXT"),
    ("kind", "TEXT"),        # "business" (pain/build) or "ai-feature" (new AI to show off)
]


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    existing = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    for col, coltype in MIGRATIONS:
        if col not in existing:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} {coltype}")
            conn.commit()
    # Rows scraped before the `kind` column existed have kind=NULL and would be invisible to
    # pool() (which filters `kind = ?`). They're all pre-pivot business items — backfill once.
    conn.execute("UPDATE items SET kind='business' WHERE kind IS NULL")
    conn.commit()
    return conn


def upsert(conn, item):
    """Insert or refresh. Preserves first_seen so novelty decay works across runs."""
    now = time.time()
    existing = conn.execute(
        "SELECT first_seen, posted FROM items WHERE id = ?", (item["id"],)
    ).fetchone()

    first_seen = existing["first_seen"] if existing else now
    posted = existing["posted"] if existing else 0

    conn.execute(
        """
        INSERT INTO items (id, source, title, body, url, score, comments,
                           created_utc, first_seen, last_seen, rank_score, theme,
                           audience, funnel_asset, why, kind, posted)
        VALUES (:id, :source, :title, :body, :url, :score, :comments,
                :created_utc, :first_seen, :last_seen, :rank_score, :theme,
                :audience, :funnel_asset, :why, :kind, :posted)
        ON CONFLICT(id) DO UPDATE SET
            score        = excluded.score,
            comments     = excluded.comments,
            last_seen    = excluded.last_seen,
            rank_score   = excluded.rank_score,
            theme        = excluded.theme,
            audience     = excluded.audience,
            funnel_asset = excluded.funnel_asset,
            why          = excluded.why,
            kind         = excluded.kind
        """,
        {
            "id": item["id"],
            "source": item.get("source", ""),
            "title": item.get("title", ""),
            "body": item.get("body") or "",
            "url": item.get("url") or "",
            "score": int(item.get("score") or 0),
            "comments": int(item.get("comments") or 0),
            "created_utc": float(item.get("created_utc") or now),
            "first_seen": first_seen,
            "last_seen": now,
            "posted": posted,
            "theme": item.get("theme") or "",
            "audience": item.get("audience") or "builder",
            "funnel_asset": item.get("funnel_asset") or "",
            "why": json.dumps(item.get("_why") or {}),
            "kind": item.get("kind") or "business",
            "rank_score": float(item.get("rank_score", 0.0)),
        },
    )
    return first_seen < now  # True if we'd seen it before


def top(conn, n=25, include_posted=False):
    q = "SELECT * FROM items"
    if not include_posted:
        q += " WHERE posted = 0"
    q += " ORDER BY rank_score DESC LIMIT ?"
    return [dict(r) for r in conn.execute(q, (n,)).fetchall()]


def pool(conn, kind=None, n=40, include_posted=True):
    """Random sample from the raw pool — no ranking. This is the post-generator's source.

    The weight system is gone: any scraped item is fair game, drawn at random so repeated
    calls keep producing fresh material ("endless"). `kind` filters business vs ai-feature.
    """
    q = "SELECT * FROM items WHERE 1=1"
    args = []
    if not include_posted:
        q += " AND posted = 0"
    if kind:
        q += " AND kind = ?"
        args.append(kind)
    q += " ORDER BY RANDOM() LIMIT ?"
    args.append(n)
    return [dict(r) for r in conn.execute(q, args).fetchall()]


def stats(conn):
    row = conn.execute(
        "SELECT COUNT(*) n, SUM(posted) p FROM items"
    ).fetchone()
    return {"total": row["n"] or 0, "posted": row["p"] or 0}
