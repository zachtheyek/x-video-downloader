"""`xdl` — the local one-shot client.

Standalone: downloads directly with yt-dlp, writes to XDL_DOWNLOAD_DIR, and records
in the local ledger. No server or queue required. Uses the exact same `run_attempt`
state machine as the server, so retries and ledger semantics are identical.
"""

from __future__ import annotations

import argparse
import sys
import time

from .config import Config
from .ledger import Ledger
from .runner import Outcome, run_attempt


def _cmd_get(args: argparse.Namespace, config: Config, ledger: Ledger) -> int:
    rc = 0
    for url in args.urls:
        job_id = ledger.add_job(url, dest=args.dest)
        print(f"[{job_id}] {url}")
        while True:
            outcome = run_attempt(ledger, config, job_id)
            job = ledger.get(job_id)
            if outcome == Outcome.SUCCESS:
                print(f"[{job_id}] ✓ {job.output_path}")
                break
            if outcome == Outcome.DEAD:
                print(f"[{job_id}] ✗ dead: {job.last_error}", file=sys.stderr)
                rc = 1
                break
            # RETRY
            print(
                f"[{job_id}] … attempt {job.attempts} failed "
                f"({job.last_error.splitlines()[0] if job.last_error else '?'}); "
                f"retrying in {config.retry_delay}s",
                file=sys.stderr,
            )
            time.sleep(config.retry_delay)
    return rc


def _cmd_ls(args: argparse.Namespace, config: Config, ledger: Ledger) -> int:
    jobs = ledger.list(status=args.status)
    if not jobs:
        print("(no jobs)")
        return 0
    for j in jobs:
        path = j.output_path or ""
        print(f"{j.id:>5}  {j.status:<8}  {j.attempts}x  {j.url}  {path}")
    return 0


def _cmd_redrive(args: argparse.Namespace, config: Config, ledger: Ledger) -> int:
    dead = ledger.dead_jobs()
    if not dead:
        print("(no dead jobs)")
        return 0
    rc = 0
    for job in dead:
        ledger.update(job.id, status="queued", attempts=0)
        print(f"[{job.id}] redrive {job.url}")
        while True:
            outcome = run_attempt(ledger, config, job.id)
            if outcome == Outcome.SUCCESS:
                print(f"[{job.id}] ✓")
                break
            if outcome == Outcome.DEAD:
                print(f"[{job.id}] ✗ still dead", file=sys.stderr)
                rc = 1
                break
            time.sleep(config.retry_delay)
    return rc


def _cmd_serve(args: argparse.Namespace, config: Config, ledger: Ledger) -> int:
    """Convenience: run the FastAPI server with an in-process Huey consumer."""
    import threading

    import uvicorn
    from huey.consumer import Consumer

    from .tasks import huey

    consumer = Consumer(huey, workers=config.workers, worker_type="thread")
    threading.Thread(target=consumer.run, daemon=True).start()
    print(f"xdl engine on http://{config.host}:{config.port} "
          f"({config.workers} workers)")
    uvicorn.run("xdl.server:app", host=config.host, port=config.port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xdl", description="X video downloader")
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("get", help="download one or more X post URLs (synchronous)")
    g.add_argument("urls", nargs="+")
    g.add_argument("--dest", choices=["downloads", "photos"], default="downloads")
    g.set_defaults(func=_cmd_get)

    ls = sub.add_parser("ls", help="list ledger jobs")
    ls.add_argument("--status", choices=sorted({"queued", "running", "success", "retrying", "dead"}))
    ls.set_defaults(func=_cmd_ls)

    rd = sub.add_parser("redrive", help="re-run every dead job")
    rd.set_defaults(func=_cmd_redrive)

    sv = sub.add_parser("serve", help="run the FastAPI engine + Huey consumer")
    sv.set_defaults(func=_cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.from_env()
    config.ensure_dirs()
    ledger = Ledger(config.db)
    return args.func(args, config, ledger)


if __name__ == "__main__":
    raise SystemExit(main())
