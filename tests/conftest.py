"""Shared fixtures. Every test points the engine's env at a tmp dir so no test
ever touches real state, the network, or X.
"""

import os

import pytest

# Point all XDL_* paths at a temp dir *before* importing anything that reads env.
@pytest.fixture
def env(tmp_path, monkeypatch):
    paths = {
        "XDL_DB": tmp_path / "ledger.db",
        "XDL_ARCHIVE": tmp_path / "archive.txt",
        "XDL_DOWNLOAD_DIR": tmp_path / "downloads",
        "XDL_STAGING_DIR": tmp_path / "staging",
        "XDL_HUEY_DB": tmp_path / "huey.db",
    }
    for k, v in paths.items():
        monkeypatch.setenv(k, str(v))
    monkeypatch.setenv("XDL_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("XDL_RETRY_DELAY", "0")
    monkeypatch.delenv("XDL_COOKIES", raising=False)
    monkeypatch.delenv("XDL_SHARED_SECRET", raising=False)
    return tmp_path


@pytest.fixture
def config(env):
    from xdl.config import Config

    cfg = Config.from_env()
    cfg.ensure_dirs()
    return cfg


@pytest.fixture
def ledger(config):
    from xdl.ledger import Ledger

    return Ledger(config.db)
