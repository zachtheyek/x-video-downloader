"""Huey (SQLite-backed) task layer: persistent queue, bounded concurrency,
delayed retries — no Redis to run.

The whole-post retry policy from the spec ("3 attempts, 60s apart") is enforced
by `run_attempt`'s attempt counter, which is authoritative over the ledger. Huey's
own `retries` is set to match so it keeps re-running the task until our logic
decides SUCCESS or DEAD and stops raising.
"""

from __future__ import annotations

from huey import SqliteHuey

from .config import Config
from .ledger import Ledger
from .runner import Outcome, run_attempt

# Built once from the environment. The server, CLI, and the Huey consumer all
# import this module, so they share one queue + one ledger as long as the env is
# consistent (it is — same .env / same container).
_config = Config.from_env()
_config.ensure_dirs()
_ledger = Ledger(_config.db)

huey = SqliteHuey(filename=str(_config.huey_db))


class RetryJob(Exception):
    """Raised to ask Huey to re-run a task after `retry_delay`."""


@huey.task(retries=_config.max_attempts, retry_delay=_config.retry_delay)
def download_task(job_id: int) -> str:
    outcome = run_attempt(_ledger, _config, job_id)
    if outcome == Outcome.RETRY:
        # Raising (with retries remaining) tells Huey to reschedule in retry_delay.
        raise RetryJob(f"job {job_id} failed transiently; will retry")
    return outcome.value


def enqueue(url: str, dest: str = "downloads") -> int:
    """Create a ledger row and hand the job to the queue. Returns the job id."""
    job_id = _ledger.add_job(url, dest)
    download_task(job_id)
    return job_id


def redrive() -> list[int]:
    """Re-enqueue every dead job. Returns the re-driven job ids."""
    ids = []
    for job in _ledger.dead_jobs():
        _ledger.update(job.id, status="queued", attempts=0)
        download_task(job.id)
        ids.append(job.id)
    return ids
