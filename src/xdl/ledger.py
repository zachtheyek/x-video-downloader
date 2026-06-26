"""The failed-link ledger (SQLite) — the source of truth for every job.

Queryable and re-drivable. Status lifecycle:

    queued -> running -> success
                      -> retrying -> running -> ... -> dead

Connections are short-lived and opened per call (cheap), with WAL enabled so the
FastAPI thread and Huey worker threads can read/write concurrently without
stepping on each other.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VALID_STATUS = {"queued", "running", "success", "retrying", "dead"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id          INTEGER PRIMARY KEY,
  url         TEXT NOT NULL,
  status      TEXT NOT NULL,            -- queued|running|success|retrying|dead
  dest        TEXT NOT NULL DEFAULT 'downloads',  -- downloads|photos
  attempts    INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT,
  output_path TEXT,
  collected   INTEGER NOT NULL DEFAULT 0,  -- 1 once an iOS collector has saved it
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    id: int
    url: str
    status: str
    dest: str
    attempts: int
    last_error: str | None
    output_path: str | None
    collected: int
    created_at: str
    updated_at: str

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "Job":
        return cls(**{k: row[k] for k in row.keys()})


class Ledger:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # --- writes ------------------------------------------------------------
    def add_job(self, url: str, dest: str = "downloads") -> int:
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO jobs (url, status, dest, created_at, updated_at) "
                "VALUES (?, 'queued', ?, ?, ?)",
                (url, dest, now, now),
            )
            return int(cur.lastrowid)

    def update(
        self,
        job_id: int,
        *,
        status: str | None = None,
        attempts: int | None = None,
        last_error: str | None = None,
        output_path: str | None = None,
        collected: int | None = None,
    ) -> None:
        if status is not None and status not in VALID_STATUS:
            raise ValueError(f"invalid status: {status!r}")
        sets, params = [], []
        for col, val in (
            ("status", status),
            ("attempts", attempts),
            ("last_error", last_error),
            ("output_path", output_path),
            ("collected", collected),
        ):
            if val is not None:
                sets.append(f"{col}=?")
                params.append(val)
        sets.append("updated_at=?")
        params.append(_now())
        params.append(job_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", params)

    def bump_attempts(self, job_id: int) -> int:
        """Atomically increment attempts and return the new value."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET attempts = attempts + 1, updated_at=? WHERE id=?",
                (_now(), job_id),
            )
            row = conn.execute(
                "SELECT attempts FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            return int(row["attempts"])

    # --- reads -------------------------------------------------------------
    def get(self, job_id: int) -> Job | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return Job._from_row(row) if row else None

    def list(self, status: str | None = None) -> list[Job]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY id", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
            return [Job._from_row(r) for r in rows]

    def uncollected(self) -> list[Job]:
        """Successful Photos-bound downloads the collector hasn't saved yet.

        Excludes archive-skipped jobs (status='success' but output_path IS NULL):
        the video was already downloaded, so there is no new file to collect.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status='success' AND dest='photos' "
                "AND collected=0 AND output_path IS NOT NULL ORDER BY id"
            ).fetchall()
            return [Job._from_row(r) for r in rows]

    def dead_urls(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT url FROM jobs WHERE status='dead' ORDER BY id"
            ).fetchall()
            return [r["url"] for r in rows]

    def dead_jobs(self) -> list[Job]:
        return self.list(status="dead")
