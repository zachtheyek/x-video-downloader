from xdl.extractor import ErrorKind, build_opts, classify_error


def test_classify_login():
    assert classify_error("NSFW tweet requires authentication. Use --cookies") == ErrorKind.LOGIN_REQUIRED
    assert classify_error("Requested content is not available, login required") == ErrorKind.LOGIN_REQUIRED
    assert classify_error("This content is age-restricted") == ErrorKind.LOGIN_REQUIRED


def test_classify_permanent():
    assert classify_error("HTTP Error 404: Not Found") == ErrorKind.PERMANENT
    assert classify_error("Unsupported URL: https://x.com/foo") == ErrorKind.PERMANENT
    assert classify_error("The account is suspended") == ErrorKind.PERMANENT


def test_classify_transient_and_default():
    assert classify_error("Unable to download webpage: timed out") == ErrorKind.TRANSIENT
    assert classify_error("rate-limit exceeded") == ErrorKind.TRANSIENT
    # unknown -> default transient (so it gets retried, not buried)
    assert classify_error("some novel error nobody has seen") == ErrorKind.TRANSIENT


def test_build_opts_uses_correct_remux_key(config, tmp_path):
    opts = build_opts(config, tmp_path)
    # The whole point of the fix: the Python-API key is `remuxvideo`, not `remux_video`.
    assert opts["remuxvideo"] == "mp4"
    assert "remux_video" not in opts
    assert opts["format"] == "bv*+ba/b"
    assert opts["merge_output_format"] == "mp4"
    assert opts["download_archive"] == str(config.archive)


def test_build_opts_sync_skips_archive(config, tmp_path):
    opts = build_opts(config, tmp_path, use_archive=False)
    assert "download_archive" not in opts


def test_build_opts_cookies(config, tmp_path):
    cookie = tmp_path / "c.txt"
    cookie.write_text("# netscape")
    opts = build_opts(config, tmp_path, cookiefile=cookie)
    assert opts["cookiefile"] == str(cookie)
