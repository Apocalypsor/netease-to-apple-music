from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import AppleTrack, MatchFailure, Song


class StateStore:
    """SQLite checkpoint store; every search and write result is committed immediately."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self._create_schema()

    def _create_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS songs (
                source_id INTEGER PRIMARY KEY,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS matches (
                source_id INTEGER PRIMARY KEY REFERENCES songs(source_id) ON DELETE CASCADE,
                catalog_id TEXT,
                title TEXT,
                artist TEXT,
                album TEXT,
                score REAL,
                status TEXT NOT NULL CHECK(status IN ('matched', 'failed')),
                error TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS writes (
                source_id INTEGER PRIMARY KEY REFERENCES songs(source_id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('writing', 'added', 'failed')),
                error TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status);
            CREATE INDEX IF NOT EXISTS idx_writes_status ON writes(status);
            """
        )
        self.db.commit()

    def ensure_identity(self, source_playlist_id: int, target_playlist_id: str) -> None:
        existing_source = self.get_meta("source_playlist_id")
        existing_target = self.get_meta("target_playlist_id")
        if existing_source and existing_source != str(source_playlist_id):
            raise ValueError("状态数据库对应了另一个网易云歌单")
        if existing_target and existing_target != target_playlist_id:
            raise ValueError("状态数据库对应了另一个 Apple Music 目标歌单")
        self.set_meta("source_playlist_id", str(source_playlist_id))
        self.set_meta("target_playlist_id", target_playlist_id)

    def sync_songs(self, songs: list[Song]) -> None:
        ids = [song.source_id for song in songs]
        with self.db:
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self.db.execute(
                    f"DELETE FROM songs WHERE source_id NOT IN ({placeholders})", ids
                )
            else:
                self.db.execute("DELETE FROM songs")
            self.db.executemany(
                """
                INSERT INTO songs(source_id, position, title, artist)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  position=excluded.position,
                  title=excluded.title,
                  artist=excluded.artist
                """,
                [(song.source_id, song.position, song.title, song.artist) for song in songs],
            )

    def recover_inflight(self) -> None:
        with self.db:
            self.db.execute("DELETE FROM writes WHERE status = 'writing'")

    def pending_matches(
        self,
        retry_failures: bool,
        limit: int | None = None,
        source_ids: set[int] | None = None,
    ) -> list[Song]:
        match_condition = "m.source_id IS NULL"
        params: list[object] = []
        if retry_failures:
            match_condition = "(m.source_id IS NULL OR m.status = 'failed')"
        conditions = [match_condition]
        if source_ids is not None:
            if not source_ids:
                return []
            placeholders = ",".join("?" for _ in source_ids)
            conditions.append(f"s.source_id IN ({placeholders})")
            params.extend(sorted(source_ids))
        query = f"""
            SELECT s.source_id, s.position, s.title, s.artist
            FROM songs s
            LEFT JOIN matches m ON m.source_id = s.source_id
            WHERE {' AND '.join(conditions)}
            ORDER BY s.position
        """
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return [Song(**dict(row)) for row in self.db.execute(query, params)]

    def save_match(self, song: Song, match: AppleTrack | None, error: str | None) -> None:
        now = _now()
        values = (
            song.source_id,
            match.catalog_id if match else None,
            match.title if match else None,
            match.artist if match else None,
            match.album if match else None,
            match.score if match else None,
            "matched" if match else "failed",
            error,
            now,
        )
        with self.db:
            self.db.execute(
                """
                INSERT INTO matches(source_id, catalog_id, title, artist, album, score, status, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  catalog_id=excluded.catalog_id,
                  title=excluded.title,
                  artist=excluded.artist,
                  album=excluded.album,
                  score=excluded.score,
                  status=excluded.status,
                  error=excluded.error,
                  updated_at=excluded.updated_at
                """,
                values,
            )

    def matched_unwritten(
        self,
        limit: int | None = None,
        source_ids: set[int] | None = None,
    ) -> list[tuple[int, AppleTrack]]:
        query = """
            SELECT s.source_id, m.catalog_id, m.title, m.artist, m.album, m.score
            FROM songs s
            JOIN matches m ON m.source_id = s.source_id AND m.status = 'matched'
            LEFT JOIN writes w ON w.source_id = s.source_id AND w.status = 'added'
            WHERE w.source_id IS NULL
            ORDER BY s.position
        """
        params: list[object] = []
        if source_ids is not None:
            if not source_ids:
                return []
            placeholders = ",".join("?" for _ in source_ids)
            query = query.replace("ORDER BY s.position", f"AND s.source_id IN ({placeholders}) ORDER BY s.position")
            params.extend(sorted(source_ids))
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = self.db.execute(query, params)
        return [
            (
                int(row["source_id"]),
                AppleTrack(
                    catalog_id=str(row["catalog_id"]),
                    title=str(row["title"]),
                    artist=str(row["artist"]),
                    album=str(row["album"] or ""),
                    score=float(row["score"]),
                ),
            )
            for row in rows
        ]

    def mark_existing_catalog_ids(self, catalog_ids: set[str]) -> int:
        if not catalog_ids:
            return 0
        placeholders = ",".join("?" for _ in catalog_ids)
        rows = self.db.execute(
            f"SELECT source_id FROM matches WHERE status='matched' AND catalog_id IN ({placeholders})",
            list(catalog_ids),
        ).fetchall()
        with self.db:
            self.db.executemany(
                """
                INSERT INTO writes(source_id, status, error, updated_at) VALUES (?, 'added', NULL, ?)
                ON CONFLICT(source_id) DO UPDATE SET status='added', error=NULL, updated_at=excluded.updated_at
                """,
                [(int(row["source_id"]), _now()) for row in rows],
            )
        return len(rows)

    def mark_writing(self, source_ids: Iterable[int]) -> None:
        with self.db:
            self.db.executemany(
                """
                INSERT INTO writes(source_id, status, error, updated_at) VALUES (?, 'writing', NULL, ?)
                ON CONFLICT(source_id) DO UPDATE SET status='writing', error=NULL, updated_at=excluded.updated_at
                """,
                [(source_id, _now()) for source_id in source_ids],
            )

    def mark_added(self, source_ids: Iterable[int]) -> None:
        with self.db:
            self.db.executemany(
                "UPDATE writes SET status='added', error=NULL, updated_at=? WHERE source_id=?",
                [(_now(), source_id) for source_id in source_ids],
            )

    def mark_failed(self, source_ids: Iterable[int], error: str) -> None:
        with self.db:
            self.db.executemany(
                "UPDATE writes SET status='failed', error=?, updated_at=? WHERE source_id=?",
                [(error, _now(), source_id) for source_id in source_ids],
            )

    def failures(self) -> list[MatchFailure]:
        rows = self.db.execute(
            """
            SELECT s.source_id, s.title, s.artist, COALESCE(m.error, w.error) AS error
            FROM songs s
            LEFT JOIN matches m ON m.source_id=s.source_id AND m.status='failed'
            LEFT JOIN writes w ON w.source_id=s.source_id AND w.status='failed'
            WHERE m.source_id IS NOT NULL OR w.source_id IS NOT NULL
            ORDER BY s.position
            """
        )
        return [
            MatchFailure(int(row["source_id"]), str(row["title"]), str(row["artist"]), str(row["error"] or "unknown"))
            for row in rows
        ]

    def counts(self) -> dict[str, int]:
        result = {"songs": 0, "matched": 0, "added": 0, "failed": 0, "pending": 0}
        result["songs"] = int(self.db.execute("SELECT COUNT(*) FROM songs").fetchone()[0])
        result["matched"] = int(self.db.execute("SELECT COUNT(*) FROM matches WHERE status='matched'").fetchone()[0])
        result["added"] = int(self.db.execute("SELECT COUNT(*) FROM writes WHERE status='added'").fetchone()[0])
        result["failed"] = len(self.failures())
        result["pending"] = result["songs"] - result["added"] - result["failed"]
        return result

    def set_meta(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else None

    def export_failures(self, output: Path) -> None:
        payload = {
            "state_db": str(self.path),
            "source_playlist_id": self.get_meta("source_playlist_id"),
            "target_playlist_id": self.get_meta("target_playlist_id"),
            "failures": [failure.__dict__ for failure in self.failures()],
        }
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def close(self) -> None:
        self.db.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
