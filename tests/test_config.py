from pathlib import Path


def test_defaults(monkeypatch):
    for k in ["XDL_DB", "XDL_ARCHIVE", "XDL_DOWNLOAD_DIR", "XDL_STAGING_DIR",
              "XDL_HUEY_DB", "XDL_COOKIES", "XDL_SHARED_SECRET", "XDL_MAX_ATTEMPTS"]:
        monkeypatch.delenv(k, raising=False)
    from xdl.config import Config

    cfg = Config.from_env()
    assert cfg.max_attempts == 3
    assert cfg.retry_delay == 60
    assert cfg.cookies is None
    assert cfg.shared_secret is None
    assert cfg.dest_dir("photos") == cfg.staging_dir
    assert cfg.dest_dir("downloads") == cfg.download_dir


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("XDL_DB", str(tmp_path / "x.db"))
    monkeypatch.setenv("XDL_MAX_ATTEMPTS", "7")
    monkeypatch.setenv("XDL_COOKIES", str(tmp_path / "c.txt"))
    monkeypatch.setenv("XDL_SHARED_SECRET", "secret")
    from xdl.config import Config

    cfg = Config.from_env()
    assert cfg.db == tmp_path / "x.db"
    assert cfg.max_attempts == 7
    assert cfg.cookies == tmp_path / "c.txt"
    assert cfg.shared_secret == "secret"


def test_bad_int_falls_back(monkeypatch):
    monkeypatch.setenv("XDL_MAX_ATTEMPTS", "not-a-number")
    from xdl.config import Config

    assert Config.from_env().max_attempts == 3
