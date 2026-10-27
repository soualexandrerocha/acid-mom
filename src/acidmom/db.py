"""Banco SQLite: itens coletados, menções extraídas e acidentes consolidados."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_item (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    collected_at TEXT NOT NULL DEFAULT (datetime('now')),
    content_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'new'  -- new, too_old, filtered_out, attack, not_relevant, extracted, error
);

CREATE TABLE IF NOT EXISTS mention (
    id INTEGER PRIMARY KEY,
    source_item_id INTEGER NOT NULL UNIQUE REFERENCES source_item(id),
    data TEXT NOT NULL  -- JSON com os campos extraídos (ver extract.SCHEMA)
);

CREATE TABLE IF NOT EXISTS incident (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    event_date TEXT,
    vessel_name TEXT,
    imo TEXT,
    vessel_type TEXT,
    event_type TEXT,
    severity TEXT,
    location TEXT,
    country TEXT,
    lat REAL,
    lon REAL,
    coords_approx INTEGER,  -- 1 quando as coordenadas são do lugar citado, não do texto
    dead INTEGER,
    missing INTEGER,
    injured INTEGER,
    pollution INTEGER,
    summary TEXT,
    alert_sent_at TEXT
);

CREATE TABLE IF NOT EXISTS incident_mention (
    incident_id INTEGER NOT NULL REFERENCES incident(id),
    mention_id INTEGER NOT NULL UNIQUE REFERENCES mention(id),
    match_score REAL NOT NULL
);
"""


@dataclass
class SourceItem:
    source: str
    url: str
    title: str
    body: str = ""
    published_at: str | None = None  # ISO 8601, UTC

    @property
    def content_hash(self) -> str:
        key = self.url.strip().lower() or (self.title + self.body)
        return hashlib.sha256(key.encode()).hexdigest()


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Bancos criados antes da fase 2 não têm a coluna coords_approx.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(incident)")}
    if "coords_approx" not in columns:
        conn.execute("ALTER TABLE incident ADD COLUMN coords_approx INTEGER")
    return conn


def save_items(conn: sqlite3.Connection, items: list[SourceItem]) -> int:
    """Grava itens novos; ignora os já vistos (mesma URL). Devolve quantos entraram."""
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO source_item (source, url, title, body, published_at, content_hash)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(i.source, i.url, i.title, i.body, i.published_at, i.content_hash) for i in items],
    )
    conn.commit()
    return conn.total_changes - before


def save_mention(conn: sqlite3.Connection, source_item_id: int, data: dict) -> int:
    cur = conn.execute(
        "INSERT INTO mention (source_item_id, data) VALUES (?, ?)",
        (source_item_id, json.dumps(data, ensure_ascii=False)),
    )
    return cur.lastrowid
