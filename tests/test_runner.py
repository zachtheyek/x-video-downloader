"""The retry/ledger state machine in run_attempt, with extractor.download mocked."""

from xdl import extractor, runner
from xdl.extractor import DownloadResult, ErrorKind
from xdl.runner import Outcome, run_attempt


def _stub_download(result: DownloadResult):
    def _fn(url, config, dest_dir, use_archive=True):
        return result

    return _fn


def test_success(ledger, config, monkeypatch):
    monkeypatch.setattr(
        extractor, "download", _stub_download(DownloadResult(ok=True, output_path="/tmp/x.mp4"))
    )
    jid = ledger.add_job("u")
    assert run_attempt(ledger, config, jid) == Outcome.SUCCESS
    job = ledger.get(jid)
    assert job.status == "success"
    assert job.output_path == "/tmp/x.mp4"
    assert job.attempts == 1


def test_transient_retries_then_dead(ledger, config, monkeypatch):
    monkeypatch.setattr(
        extractor,
        "download",
        _stub_download(DownloadResult(ok=False, error="timeout", error_kind=ErrorKind.TRANSIENT)),
    )
    jid = ledger.add_job("u")
    # max_attempts=3 (from env): two RETRYs then DEAD.
    assert run_attempt(ledger, config, jid) == Outcome.RETRY
    assert ledger.get(jid).status == "retrying"
    assert run_attempt(ledger, config, jid) == Outcome.RETRY
    assert run_attempt(ledger, config, jid) == Outcome.DEAD

    job = ledger.get(jid)
    assert job.status == "dead"
    assert job.attempts == 3
    assert job.last_error == "timeout"
    # dead URL also mirrored into failed.txt
    failed = (config.db.parent / "failed.txt").read_text().splitlines()
    assert failed == ["u"]


def test_permanent_is_dead_on_first_attempt(ledger, config, monkeypatch):
    monkeypatch.setattr(
        extractor,
        "download",
        _stub_download(DownloadResult(ok=False, error="404", error_kind=ErrorKind.PERMANENT)),
    )
    jid = ledger.add_job("u")
    assert run_attempt(ledger, config, jid) == Outcome.DEAD
    assert ledger.get(jid).attempts == 1  # no wasted retries


def test_missing_job_raises(ledger, config):
    import pytest

    with pytest.raises(KeyError):
        run_attempt(ledger, config, 999)
