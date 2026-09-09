from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .events import emit


VIDEO_HOSTS = {
    "youtube.com": "YouTube",
    "www.youtube.com": "YouTube",
    "m.youtube.com": "YouTube",
    "music.youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "bilibili.com": "Bilibili",
    "www.bilibili.com": "Bilibili",
    "m.bilibili.com": "Bilibili",
    "b23.tv": "Bilibili",
    "www.b23.tv": "Bilibili",
}


@dataclass(frozen=True)
class OnlineVideoMedia:
    video_id: str
    title: str
    webpage_url: str
    platform: str
    path: Path | None = None


YouTubeMedia = OnlineVideoMedia


def platform_for_url(value: str) -> str | None:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    return VIDEO_HOSTS.get((parsed.hostname or "").lower())


def is_online_video_url(value: str) -> bool:
    return platform_for_url(value) is not None


def is_youtube_url(value: str) -> bool:
    return platform_for_url(value) == "YouTube"


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    result: list[str] = []
    used = 0
    for character in value:
        size = len(character.encode("utf-8"))
        if used + size > maximum_bytes:
            break
        result.append(character)
        used += size
    return "".join(result).rstrip("._-")


def safe_title_component(title: str, fallback: str = "video", maximum_bytes: int = 160) -> str:
    """Return a Unicode-safe APFS filename component without splitting characters."""
    normalized = unicodedata.normalize("NFC", str(title or ""))
    pieces: list[str] = []
    separator_pending = False
    for character in normalized:
        category = unicodedata.category(character)
        if category[0] in {"L", "M", "N"}:
            if separator_pending and pieces and pieces[-1] != "_":
                pieces.append("_")
            pieces.append(character)
            separator_pending = False
        elif character in {"-", "_"}:
            if pieces and pieces[-1] != character:
                pieces.append(character)
            separator_pending = False
        elif character.isspace() or category[0] in {"P", "S", "Z"}:
            separator_pending = True

    cleaned = re.sub(r"_+", "_", "".join(pieces)).strip("._-")
    meaningful = sum(character.isalnum() for character in cleaned)
    if meaningful < 2:
        cleaned = fallback
    return _truncate_utf8(cleaned, maximum_bytes) or fallback


def safe_identifier(value: str, fallback: str = "video") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", value or "")
    return cleaned[:48].strip("_-") or fallback


def safe_submission_date(value: str | None = None) -> str:
    if value:
        try:
            datetime.strptime(value, "%Y%m%d")
            return value
        except ValueError as error:
            raise ValueError("Submission date must use YYYYMMDD format.") from error
    return datetime.now().astimezone().strftime("%Y%m%d")


def safe_output_name(title: str, video_id: str = "", submission_date: str | None = None) -> str:
    """Build the requested YYYYMMDD_title name; collision suffixes are added later."""
    del video_id
    return f"{safe_submission_date(submission_date)}_{safe_title_component(title)}"


def _media_from_info(
    info: dict[str, Any],
    platform: str,
    path: Path | None = None,
) -> OnlineVideoMedia:
    video_id = str(info.get("id") or "").strip()
    if not video_id:
        raise RuntimeError(f"{platform} did not return a video ID for this link.")
    title = str(info.get("title") or f"{platform} video").strip() or f"{platform} video"
    webpage_url = str(info.get("webpage_url") or info.get("original_url") or "").strip()
    return OnlineVideoMedia(
        video_id=video_id,
        title=title,
        webpage_url=webpage_url,
        platform=platform,
        path=path,
    )


def _options() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "format": "bestaudio/best",
        "ffmpeg_location": "/opt/homebrew/bin",
    }


def inspect_online_video(url: str) -> OnlineVideoMedia:
    platform = platform_for_url(url)
    if platform is None:
        raise ValueError("Enter a valid YouTube, youtu.be, Bilibili, or b23.tv video link.")
    try:
        import yt_dlp
    except ImportError as error:
        raise RuntimeError("Online-video support is not installed. Rebuild the app dependencies.") from error

    emit("status", message=f"Checking {platform} link")
    try:
        with yt_dlp.YoutubeDL({**_options(), "skip_download": True}) as downloader:
            info = downloader.extract_info(url.strip(), download=False)
    except yt_dlp.utils.DownloadError as error:
        if platform == "Bilibili" and "412" in str(error):
            raise RuntimeError(
                "Bilibili blocked this request (HTTP 412). The link is supported, but Bilibili "
                "is refusing access from this network right now; open it in a browser and retry later."
            ) from error
        raise RuntimeError(f"Could not read this {platform} video: {error}") from error
    if info.get("_type") == "playlist":
        raise ValueError(f"Use a link to one {platform} video, not a playlist.")
    return _media_from_info(info, platform)


def download_online_video(url: str, directory: Path) -> OnlineVideoMedia:
    platform = platform_for_url(url)
    if platform is None:
        raise ValueError("Enter a supported YouTube or Bilibili video link.")
    try:
        import yt_dlp
    except ImportError as error:
        raise RuntimeError("Online-video support is not installed. Rebuild the app dependencies.") from error

    directory.mkdir(parents=True, exist_ok=True)
    emit("status", message=f"Downloading audio from {platform}")

    def progress_hook(event: dict[str, Any]) -> None:
        if event.get("status") != "downloading":
            return
        total = event.get("total_bytes") or event.get("total_bytes_estimate")
        downloaded = event.get("downloaded_bytes")
        if total and downloaded is not None:
            emit(
                "download_progress",
                value=min(1.0, float(downloaded) / float(total)),
                eta=event.get("eta"),
                platform=platform,
            )

    options = {
        **_options(),
        "outtmpl": str(directory / "online-%(id)s.%(ext)s"),
        "progress_hooks": [progress_hook],
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url.strip(), download=True)
            expected = Path(downloader.prepare_filename(info))
    except yt_dlp.utils.DownloadError as error:
        if platform == "Bilibili" and "412" in str(error):
            raise RuntimeError(
                "Bilibili blocked this request (HTTP 412). Open the video in a browser and retry later."
            ) from error
        raise RuntimeError(f"Could not download audio from {platform}: {error}") from error

    candidates = [expected]
    candidates.extend(directory.glob(f"online-{info.get('id', '')}.*"))
    downloaded = next((candidate for candidate in candidates if candidate.is_file()), None)
    if downloaded is None:
        raise RuntimeError(f"{platform} audio downloaded, but the media file could not be found.")
    return _media_from_info(info, platform, downloaded)


def inspect_youtube(url: str) -> OnlineVideoMedia:
    if not is_youtube_url(url):
        raise ValueError("Enter a valid youtube.com or youtu.be video link.")
    return inspect_online_video(url)


def download_youtube(url: str, directory: Path) -> OnlineVideoMedia:
    if not is_youtube_url(url):
        raise ValueError("Enter a valid youtube.com or youtu.be video link.")
    return download_online_video(url, directory)
