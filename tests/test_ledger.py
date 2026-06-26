def test_add_and_get(ledger):
    jid = ledger.add_job("https://x.com/a/status/1", dest="photos")
    job = ledger.get(jid)
    assert job.url == "https://x.com/a/status/1"
    assert job.status == "queued"
    assert job.dest == "photos"
    assert job.attempts == 0
    assert job.collected == 0


def test_bump_attempts(ledger):
    jid = ledger.add_job("u")
    assert ledger.bump_attempts(jid) == 1
    assert ledger.bump_attempts(jid) == 2
    assert ledger.get(jid).attempts == 2


def test_update_status_and_invalid(ledger):
    import pytest

    jid = ledger.add_job("u")
    ledger.update(jid, status="success", output_path="/tmp/x.mp4")
    job = ledger.get(jid)
    assert job.status == "success"
    assert job.output_path == "/tmp/x.mp4"
    with pytest.raises(ValueError):
        ledger.update(jid, status="bogus")


def test_list_filtering_and_dead(ledger):
    a = ledger.add_job("a")
    b = ledger.add_job("b")
    ledger.update(a, status="dead", last_error="boom")
    ledger.update(b, status="success")
    assert ledger.dead_urls() == ["a"]
    assert {j.id for j in ledger.list(status="success")} == {b}
    assert len(ledger.list()) == 2


def test_uncollected_only_photos_success(ledger):
    a = ledger.add_job("a", dest="photos")
    b = ledger.add_job("b", dest="downloads")
    c = ledger.add_job("c", dest="photos")
    d = ledger.add_job("d", dest="photos")
    ledger.update(a, status="success", output_path="/tmp/a.mp4")
    ledger.update(b, status="success", output_path="/tmp/b.mp4")
    ledger.update(c, status="success", output_path="/tmp/c.mp4", collected=1)
    # d: archive-skipped -> success but no output_path -> nothing to collect
    ledger.update(d, status="success")
    # only `a`: photos + success + not collected + has a file
    assert [j.id for j in ledger.uncollected()] == [a]
