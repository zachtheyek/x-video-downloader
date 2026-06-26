"""Anonymous-first / cookie-fallback logic, with a fake YoutubeDL (no network)."""

import dataclasses
import os

from yt_dlp.utils import DownloadError

from xdl import extractor


def _fake_ydl_factory(behavior, calls):
    """behavior(opts) -> ('ok', path) | ('raise', message). Records opts in `calls`."""

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            calls.append(self.opts)
            kind, payload = behavior(self.opts)
            if kind == "raise":
                raise DownloadError(payload)
            os.makedirs(os.path.dirname(payload), exist_ok=True)
            with open(payload, "wb") as fh:
                fh.write(b"\x00\x00")
            return {"requested_downloads": [{"filepath": payload}]}

    return FakeYDL


def test_anonymous_success_no_cookie_retry(config, tmp_path, monkeypatch):
    dest = config.download_dir
    out = str(dest / "vid.mp4")
    calls = []
    monkeypatch.setattr(extractor, "YoutubeDL", _fake_ydl_factory(lambda o: ("ok", out), calls))

    result = extractor.download("https://x.com/a/status/1", config, dest)
    assert result.ok
    assert result.used_cookies is False
    assert len(calls) == 1  # never reached for cookies


def test_login_wall_triggers_cookie_retry(config, tmp_path, monkeypatch):
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape\nfake")
    cfg = dataclasses.replace(config, cookies=cookie)
    out = str(cfg.download_dir / "vid.mp4")
    calls = []

    def behavior(opts):
        if "cookiefile" in opts:
            return ("ok", out)
        return ("raise", "NSFW tweet requires authentication. Use --cookies")

    monkeypatch.setattr(extractor, "YoutubeDL", _fake_ydl_factory(behavior, calls))
    result = extractor.download("https://x.com/a/status/1", cfg, cfg.download_dir)
    assert result.ok
    assert result.used_cookies is True
    assert len(calls) == 2  # anonymous, then cookie


def test_login_wall_no_cookies_configured_fails(config, monkeypatch):
    calls = []
    monkeypatch.setattr(
        extractor,
        "YoutubeDL",
        _fake_ydl_factory(lambda o: ("raise", "login required"), calls),
    )
    result = extractor.download("u", config, config.download_dir)
    assert result.ok is False
    assert result.error_kind == extractor.ErrorKind.LOGIN_REQUIRED
    assert len(calls) == 1  # no cookies -> no retry


def test_permanent_error_never_uses_cookies(config, tmp_path, monkeypatch):
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape\nfake")
    cfg = dataclasses.replace(config, cookies=cookie)
    calls = []
    monkeypatch.setattr(
        extractor,
        "YoutubeDL",
        _fake_ydl_factory(lambda o: ("raise", "HTTP Error 404: Not Found"), calls),
    )
    result = extractor.download("u", cfg, cfg.download_dir)
    assert result.ok is False
    assert result.error_kind == extractor.ErrorKind.PERMANENT
    assert len(calls) == 1  # permanent -> no cookie retry
