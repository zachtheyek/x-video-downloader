"""HTTP surface tests. Huey runs in immediate mode (tasks execute inline) and the
downloader is faked, so the full request->ledger path is exercised without network.
"""

import importlib
import uuid

from fastapi.testclient import TestClient

from xdl import extractor
from xdl.extractor import DownloadResult, ErrorKind


def reload_engine():
    """Re-import the env-bound modules so they pick up the test's tmp paths."""
    import xdl.server as server
    import xdl.tasks as tasks

    importlib.reload(tasks)
    importlib.reload(server)
    tasks.huey.immediate = True  # execute enqueued tasks inline
    return server, tasks


def fake_download_ok(monkeypatch):
    def _dl(url, config, dest_dir, use_archive=True):
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = str(dest_dir / f"{uuid.uuid4().hex}.mp4")
        with open(path, "wb") as fh:
            fh.write(b"VIDEOBYTES")
        return DownloadResult(ok=True, output_path=path)

    monkeypatch.setattr(extractor, "download", _dl)


def fake_download_fail(monkeypatch, kind=ErrorKind.PERMANENT):
    def _dl(url, config, dest_dir, use_archive=True):
        return DownloadResult(ok=False, error="nope", error_kind=kind)

    monkeypatch.setattr(extractor, "download", _dl)


def test_healthz(env):
    server, _ = reload_engine()
    c = TestClient(server.app)
    assert c.get("/healthz").json()["ok"] is True


def test_async_job_runs_and_records(env, monkeypatch):
    fake_download_ok(monkeypatch)
    server, _ = reload_engine()
    c = TestClient(server.app)
    r = c.post("/jobs", json={"urls": ["https://x.com/a/status/1"], "mode": "async"})
    assert r.status_code == 200
    assert len(r.json()["jobs"]) == 1
    # immediate mode -> already processed
    done = c.get("/jobs", params={"status": "success"}).json()["jobs"]
    assert len(done) == 1


def test_async_batch_independent_jobs(env, monkeypatch):
    fake_download_ok(monkeypatch)
    server, _ = reload_engine()
    c = TestClient(server.app)
    urls = [f"https://x.com/a/status/{i}" for i in range(3)]
    r = c.post("/jobs", json={"urls": urls, "mode": "async"})
    assert len(r.json()["jobs"]) == 3
    assert len(c.get("/jobs", params={"status": "success"}).json()["jobs"]) == 3


def test_sync_returns_file_bytes(env, monkeypatch):
    fake_download_ok(monkeypatch)
    server, _ = reload_engine()
    c = TestClient(server.app)
    r = c.post("/jobs", json={"urls": ["u"], "mode": "sync", "dest": "photos"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert r.content == b"VIDEOBYTES"


def test_sync_failure_queues_202(env, monkeypatch):
    fake_download_fail(monkeypatch, kind=ErrorKind.PERMANENT)
    server, _ = reload_engine()
    c = TestClient(server.app)
    r = c.post("/jobs", json={"urls": ["u"], "mode": "sync", "dest": "photos"})
    assert r.status_code == 202
    assert r.json()["status"] == "queued"


def test_collector_flow(env, monkeypatch):
    fake_download_ok(monkeypatch)
    server, _ = reload_engine()
    c = TestClient(server.app)
    c.post("/jobs", json={"urls": ["u"], "mode": "async", "dest": "photos"})

    completed = c.get("/completed").json()["completed"]
    assert len(completed) == 1
    jid = completed[0]["id"]
    assert completed[0]["download_url"] == f"/files/{jid}"

    assert c.get(f"/files/{jid}").content == b"VIDEOBYTES"
    assert c.post(f"/completed/{jid}/ack").json()["ok"] is True
    # acked -> no longer offered
    assert c.get("/completed").json()["completed"] == []


def test_redrive(env, monkeypatch):
    fake_download_fail(monkeypatch, kind=ErrorKind.PERMANENT)
    server, _ = reload_engine()
    c = TestClient(server.app)
    c.post("/jobs", json={"urls": ["u"], "mode": "async"})
    assert len(c.get("/jobs", params={"status": "dead"}).json()["jobs"]) == 1
    r = c.post("/failures/redrive")
    assert len(r.json()["redriven"]) == 1


def test_shared_secret_enforced(env, monkeypatch):
    monkeypatch.setenv("XDL_SHARED_SECRET", "s3cret")
    fake_download_ok(monkeypatch)
    server, _ = reload_engine()
    c = TestClient(server.app)
    assert c.post("/jobs", json={"urls": ["u"]}).status_code == 401
    ok = c.post("/jobs", json={"urls": ["u"]}, headers={"X-XDL-Token": "s3cret"})
    assert ok.status_code == 200
