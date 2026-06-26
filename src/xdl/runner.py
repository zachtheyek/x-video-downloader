"""Per-attempt orchestration: run one download attempt and record the outcome.

Kept independent of Huey so the retry/ledger state machine is testable without a
queue. Huey (in `tasks.py`) is a thin scheduler that calls `run_attempt` and
re-runs it after a delay when the outcome is RETRY.
"""

from __future__ import annotations

from enum import Enum

from . import extractor
from .config import Config
from .extractor import ErrorKind
from .ledger import Ledger


class Outcome(str, Enum):
    SUCCESS = "success"
    RETRY = "retry"
    DEAD = "dead"


def _append_failed(config: Config, url: str) -> None:
    """Mirror dead URLs into a dead-simple text artifact next to the ledger."""
    path = config.db.parent / "failed.txt"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(url + "\n")


def run_attempt(ledger: Ledger, config: Config, job_id: int) -> Outcome:
    """Run one attempt for a job and transition its ledger status.

    Returns:
        SUCCESS — downloaded (or already in the archive); stop.
        RETRY   — transient/login failure with attempts remaining; caller reschedules.
        DEAD    — permanent failure, or attempts exhausted; buried in the ledger.
    """
    job = ledger.get(job_id)
    if job is None:
        raise KeyError(f"job {job_id} not found")

    attempt = ledger.bump_attempts(job_id)
    ledger.update(job_id, status="running")

    dest_dir = config.dest_dir(job.dest)
    result = extractor.download(job.url, config, dest_dir)

    if result.ok:
        ledger.update(job_id, status="success", output_path=result.output_path)
        return Outcome.SUCCESS

    exhausted = attempt >= config.max_attempts
    permanent = result.error_kind == ErrorKind.PERMANENT
    if permanent or exhausted:
        ledger.update(job_id, status="dead", last_error=result.error)
        _append_failed(config, job.url)
        return Outcome.DEAD

    ledger.update(job_id, status="retrying", last_error=result.error)
    return Outcome.RETRY
