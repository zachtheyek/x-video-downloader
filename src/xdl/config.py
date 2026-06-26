"""Environment-driven configuration.

Every path the engine touches is read from the environment so that moving state
(e.g. to a NAS) is a config change, not a code change. `Config.from_env()` is the
single entry point; nothing else in the codebase reads `os.environ` for paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _path(env: str, default: str) -> Path:
    return Path(os.environ.get(env, default)).expanduser()


def _int(env: str, default: int) -> int:
    try:
        return int(os.environ[env])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    db: Path
    archive: Path
    download_dir: Path
    staging_dir: Path
    huey_db: Path
    cookies: Path | None
    max_attempts: int
    retry_delay: int
    workers: int
    fragments: int
    host: str
    port: int
    shared_secret: str | None

    @classmethod
    def from_env(cls) -> "Config":
        cookies_raw = os.environ.get("XDL_COOKIES")
        cookies = Path(cookies_raw).expanduser() if cookies_raw else None
        return cls(
            db=_path("XDL_DB", "./data/ledger.db"),
            archive=_path("XDL_ARCHIVE", "./data/archive.txt"),
            download_dir=_path("XDL_DOWNLOAD_DIR", "./data/downloads"),
            staging_dir=_path("XDL_STAGING_DIR", "./data/staging"),
            huey_db=_path("XDL_HUEY_DB", "./data/huey.db"),
            cookies=cookies,
            max_attempts=_int("XDL_MAX_ATTEMPTS", 3),
            retry_delay=_int("XDL_RETRY_DELAY", 60),
            workers=_int("XDL_WORKERS", 4),
            fragments=_int("XDL_FRAGMENTS", 4),
            host=os.environ.get("XDL_HOST", "0.0.0.0"),
            port=_int("XDL_PORT", 8080),
            shared_secret=os.environ.get("XDL_SHARED_SECRET") or None,
        )

    def ensure_dirs(self) -> None:
        """Create the directories the engine writes into (idempotent)."""
        for p in (self.db, self.archive, self.huey_db):
            p.parent.mkdir(parents=True, exist_ok=True)
        for d in (self.download_dir, self.staging_dir):
            d.mkdir(parents=True, exist_ok=True)

    def dest_dir(self, dest: str) -> Path:
        """Map a job's `dest` to the directory its file should land in."""
        return self.staging_dir if dest == "photos" else self.download_dir
