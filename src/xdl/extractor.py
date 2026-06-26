"""yt-dlp extraction with error classification and anonymous-first auth.

Two ideas live here:

1. **Highest-quality, no-transcode options** for X (see `build_opts`). X video is
   already H.264/AAC, so we merge/remux to an MP4 container but never re-encode.
2. **Anonymous-first, cookie-fallback** (`download`): try with no account at all;
   only if X returns a login/sensitive-content wall do we retry the same URL with
   the burner cookies. Passing cookies can *break* downloads that work anonymously,
   so cookies are strictly a fallback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .config import Config


class ErrorKind(str, Enum):
    LOGIN_REQUIRED = "login_required"  # retry with cookies may help
    TRANSIENT = "transient"            # network blip / rate-limit -> retry later
    PERMANENT = "permanent"            # deleted / unsupported -> straight to dead


# Substring markers matched against the lowercased error message. Ordered checks:
# login first (X literally tells you to use cookies for NSFW/sensitive), then
# permanent, then transient. Unknown errors default to TRANSIENT so they get a
# retry rather than being prematurely buried as dead.
_LOGIN_MARKERS = (
    "nsfw",
    "age-restricted",
    "age restricted",
    "login required",
    "log in",
    "logged in",
    "sign in",
    "requires authentication",
    "authentication",
    "sensitive",
    "requested content is not available",
)
_PERMANENT_MARKERS = (
    "no video could be found",
    "no video formats found",
    "unsupported url",
    "does not exist",
    "no longer exists",
    "has been deleted",
    "account is suspended",
    "404",
    "not found",
    "unable to extract",
)
_TRANSIENT_MARKERS = (
    "rate-limit",
    "rate limit",
    "timed out",
    "timeout",
    "temporarily",
    "try again",
    "connection",
    "503",
    "502",
    "500",
    "unable to download webpage",
)


def classify_error(error: str | BaseException) -> ErrorKind:
    msg = str(error).lower()
    if any(m in msg for m in _LOGIN_MARKERS):
        return ErrorKind.LOGIN_REQUIRED
    if any(m in msg for m in _PERMANENT_MARKERS):
        return ErrorKind.PERMANENT
    if any(m in msg for m in _TRANSIENT_MARKERS):
        return ErrorKind.TRANSIENT
    return ErrorKind.TRANSIENT


@dataclass
class DownloadResult:
    ok: bool
    output_path: str | None = None
    error: str | None = None
    error_kind: ErrorKind | None = None
    used_cookies: bool = False


def build_opts(
    config: Config,
    dest_dir: Path,
    *,
    cookiefile: Path | None = None,
    use_archive: bool = True,
    progress_hooks: list | None = None,
) -> dict:
    """Canonical highest-quality yt-dlp options for X.

    `use_archive=False` is used by sync one-offs: the caller explicitly wants
    *this* file now, so we must not let the dedup archive skip the download.
    """
    opts: dict = {
        "format": "bv*+ba/b",
        "format_sort": ["res", "vbr", "abr"],
        "merge_output_format": "mp4",
        "remuxvideo": "mp4",  # NB: yt-dlp Python key is `remuxvideo`, not `remux_video`
        "concurrent_fragment_downloads": config.fragments,
        "retries": 10,
        "fragment_retries": 10,
        "retry_sleep_functions": {"http": lambda n: min(5, n)},
        "outtmpl": str(dest_dir / "%(uploader_id)s-%(id)s.%(ext)s"),
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
    }
    if use_archive:
        opts["download_archive"] = str(config.archive)
    if cookiefile is not None:
        opts["cookiefile"] = str(cookiefile)
    if progress_hooks:
        opts["progress_hooks"] = progress_hooks
    return opts


def _output_path(info: dict) -> str | None:
    """Best-effort resolution of the final file path after post-processing."""
    downloads = info.get("requested_downloads")
    if downloads:
        # filepath reflects the post-merge/remux name; fall back to _filename.
        first = downloads[0]
        return first.get("filepath") or first.get("_filename")
    return info.get("filepath") or info.get("_filename")


def _attempt(
    url: str, config: Config, dest_dir: Path, cookiefile: Path | None, use_archive: bool
) -> DownloadResult:
    opts = build_opts(config, dest_dir, cookiefile=cookiefile, use_archive=use_archive)
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        path = _output_path(info or {})
        # An archive hit returns info but skips the download (path may be absent).
        # That still counts as success: we already have the video.
        return DownloadResult(
            ok=True, output_path=path, used_cookies=cookiefile is not None
        )
    except DownloadError as e:
        return DownloadResult(
            ok=False,
            error=str(e),
            error_kind=classify_error(e),
            used_cookies=cookiefile is not None,
        )
    except Exception as e:  # pragma: no cover - defensive
        return DownloadResult(
            ok=False, error=repr(e), error_kind=ErrorKind.TRANSIENT,
            used_cookies=cookiefile is not None,
        )


def download(url: str, config: Config, dest_dir: Path, *, use_archive: bool = True) -> DownloadResult:
    """Anonymous-first download with a single cookie-based retry on login walls."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    result = _attempt(url, config, dest_dir, cookiefile=None, use_archive=use_archive)
    if result.ok:
        return result

    cookies_usable = (
        result.error_kind == ErrorKind.LOGIN_REQUIRED
        and config.cookies is not None
        and config.cookies.exists()
        and os.path.getsize(config.cookies) > 0
    )
    if cookies_usable:
        return _attempt(url, config, dest_dir, cookiefile=config.cookies, use_archive=use_archive)
    return result
