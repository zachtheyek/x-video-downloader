"""FastAPI surface for the engine.

Endpoints map 1:1 onto the spec. Sync mode runs inline and returns the MP4 bytes
(so the iOS share sheet can Save to Photos immediately); on failure it auto-enqueues
to the async queue and returns 202 `queued`, so a failed sync download is never lost.
"""

from __future__ import annotations

import os
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import extractor, tasks
from .config import Config
from .ledger import Ledger

config = Config.from_env()
config.ensure_dirs()
ledger = Ledger(config.db)

app = FastAPI(title="xdl", version="0.1.0")


def require_token(x_xdl_token: str | None = Header(default=None)) -> None:
    """Optional shared-secret check (belt-and-suspenders on top of Tailscale)."""
    if config.shared_secret and x_xdl_token != config.shared_secret:
        raise HTTPException(status_code=401, detail="bad or missing X-XDL-Token")


class JobsRequest(BaseModel):
    urls: list[str]
    mode: Literal["async", "sync"] = "async"
    dest: Literal["downloads", "photos"] = "downloads"


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "version": app.version}


@app.post("/jobs", dependencies=[Depends(require_token)])
def post_jobs(req: JobsRequest):
    if not req.urls:
        raise HTTPException(status_code=400, detail="no urls")

    if req.mode == "sync":
        return _run_sync(req.urls[0], req.dest)

    # async: each URL is an independent job so one bad link can't fail a batch.
    created = [{"id": tasks.enqueue(u, req.dest), "url": u} for u in req.urls]
    return {"mode": "async", "jobs": created}


def _run_sync(url: str, dest: str):
    """Download inline and return the file bytes; on failure, queue it (202)."""
    job_id = ledger.add_job(url, dest)
    ledger.bump_attempts(job_id)
    ledger.update(job_id, status="running")
    dest_dir = config.dest_dir(dest)
    # use_archive=False: a sync one-off must produce the file even if we already have it.
    result = extractor.download(url, config, dest_dir, use_archive=False)

    path = result.output_path
    if result.ok and path and os.path.exists(path):
        # collected=1 so the Tier-2 collector won't also re-serve a file we just handed back.
        ledger.update(job_id, status="success", output_path=path, collected=1)
        return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))

    # Failed (or archive-skipped with no bytes): hand off to the full async pipeline.
    ledger.update(job_id, status="queued", attempts=0, last_error=result.error)
    tasks.download_task(job_id)
    return JSONResponse(
        {"status": "queued", "job_id": job_id, "reason": result.error},
        status_code=202,
    )


@app.get("/jobs", dependencies=[Depends(require_token)])
def get_jobs(status: str | None = None):
    return {"jobs": [j.__dict__ for j in ledger.list(status=status)]}


@app.get("/completed", dependencies=[Depends(require_token)])
def get_completed():
    """Finished-but-uncollected Photos files for the iOS collector to pull."""
    return {
        "completed": [
            {"id": j.id, "url": j.url, "download_url": f"/files/{j.id}"}
            for j in ledger.uncollected()
            if j.output_path and os.path.exists(j.output_path)
        ]
    }


@app.get("/files/{job_id}", dependencies=[Depends(require_token)])
def get_file(job_id: int):
    job = ledger.get(job_id)
    if job is None or not job.output_path or not os.path.exists(job.output_path):
        raise HTTPException(status_code=404, detail="file not available")
    return FileResponse(
        job.output_path, media_type="video/mp4", filename=os.path.basename(job.output_path)
    )


@app.post("/completed/{job_id}/ack", dependencies=[Depends(require_token)])
def ack(job_id: int):
    job = ledger.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such job")
    ledger.update(job_id, collected=1)
    return {"ok": True, "id": job_id}


@app.post("/failures/redrive", dependencies=[Depends(require_token)])
def redrive():
    return {"redriven": tasks.redrive()}
