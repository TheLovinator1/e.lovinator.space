from __future__ import annotations

import binascii
import html as html_module
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address
from ipaddress import IPv6Address
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated
from typing import Any
from urllib.parse import quote
from urllib.parse import unquote
from urllib.parse import urlsplit

import niquests
from anyio import to_thread
from gallery_dl import config
from gallery_dl import job
from gallery_dl.extractor.message import Message
from htpy import head
from htpy import html
from htpy import link
from htpy import meta
from litestar import Request
from litestar import get
from litestar.background_tasks import BackgroundTask
from litestar.params import PathParameter
from litestar.response import Redirect
from litestar.response import Response
from loguru import logger
from selectolax.parser import HTMLParser

from e.activity import DEFAULT_AUTHOR_TEXT
from e.activity import compact_number
from e.activity import engagement_text
from e.activity import oembed_payload
from e.activity import status_payload
from e.discord import DiscordIPs
from e.discord import get_discord_ips
from e.settings import ARCHIVE_PATH
from e.settings import NITTER_INSTANCE
from e.settings import ORIGINAL_URL
from e.settings import TWITTER_MEDIA_DIR
from e.translate import translate_embed

CONTENT_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
    "m4v": "video/mp4",
    "mp3": "audio/mpeg",
}
"""Mapping of file extensions to MIME types."""


@dataclass(frozen=True, slots=True)
class Media:
    """A media file belonging to a tweet."""

    url: str
    """Absolute public URL the file is served from."""

    content_type: str
    """MIME type of the file."""

    width: int | None = None
    """Pixel width, used for video player embeds."""

    height: int | None = None
    """Pixel height, used for video player embeds."""

    @property
    def is_video(self) -> bool:
        """Whether the file is a video."""
        return self.content_type.startswith("video/")


@dataclass(frozen=True, slots=True)
class Embed:
    """Everything needed to render an Open Graph embed page."""

    title: str
    """Embed title."""

    description: str
    """Embed description (the tweet text)."""

    url: str
    """Canonical URL of the original tweet."""

    media: tuple[Media, ...]
    """Media files to embed."""

    poster: str | None = None
    """Thumbnail URL shown for video embeds."""

    site: str | None = None
    """Author handle for the ``twitter:site`` tag."""

    creator: str | None = None
    """Author handle for the ``twitter:creator`` tag."""

    stats: tuple[tuple[str, str], ...] = ()
    """(label, value) pairs rendered as ``twitter:labelN``/``twitter:dataN``.

    Discord renders the first two pairs as fields in the embed.
    """


def configure_extractor() -> None:
    """Configure gallery-dl's Nitter extractor."""
    config.set(
        path=("extractor",),
        key="base-directory",
        value=str(TWITTER_MEDIA_DIR),
    )
    config.set(
        path=("extractor", "nitter"),
        key="quoted",
        value=True,
    )
    config.set(
        path=("extractor", "nitter"),
        key="retweets",
        value=True,
    )
    config.set(
        path=("extractor", "nitter"),
        key="videos",
        value="ytdl",
    )
    config.set(
        path=("extractor", "nitter"),
        key="directory",
        value=["{author['name']}", "{tweet_id}"],
    )
    config.set(
        path=("extractor", "nitter"),
        key="filename",
        value="{num}.{extension}",
    )
    config.set(
        path=("extractor",),
        key="archive",
        value=str(ARCHIVE_PATH),
    )
    config.set(
        path=("extractor",),
        key="archive-pragma",
        value=["journal_mode=WAL", "synchronous=NORMAL"],
    )


def content_type_for(filename: str | Path) -> str:
    """Return the MIME type for a media filename.

    Args:
        filename: The media filename or path.

    Returns:
        The MIME type, or ``application/octet-stream`` for unknown types.
    """
    extension = Path(filename).suffix.lower().lstrip(".")
    return CONTENT_TYPES.get(extension, "application/octet-stream")


def is_media_file(path: Path) -> bool:
    """Whether a path is a supported media file.

    Args:
        path: The path to check.

    Returns:
        ``True`` if the file has a supported media extension.
    """
    return path.is_file() and path.suffix.lower().lstrip(".") in CONTENT_TYPES


_META_CACHE_TTL = 10 * 60
"""How long fetched tweet metadata is kept in memory."""

_meta_cache: dict[str, tuple[float, tuple[dict[str, Any], list[dict[str, Any]]] | None]] = {}
"""In-memory cache of ``nitter_url -> (fetched_at, (meta, media_items))``.

Discord requests the embed page, the activity document and the oEmbed
document for every posted link; the cache stops those from each hitting Nitter.
"""

_AVATAR_CACHE_TTL = 24 * 60 * 60
"""How long a resolved profile image URL is kept in memory."""

_avatar_cache: dict[str, tuple[float, str | None]] = {}
"""In-memory cache of ``username -> (fetched_at, avatar_url)``."""

_AVATAR_RE = re.compile(r'<img class="avatar[^"]*" src="([^"]+)"')


def avatar_from_profile(html: str) -> str | None:
    """Extract the profile image URL from a Nitter profile page.

    Args:
        html: The profile page HTML.

    Returns:
        The absolute avatar URL, or ``None`` if it cannot be found.
    """
    match = _AVATAR_RE.search(html)
    if match is None:
        return None
    url = html_module.unescape(match.group(1))
    return url.replace("_bigger.", "_400x400.") if "_bigger." in url else url


def _fetch_profile(username: str) -> str | None:
    """Fetch a Nitter profile page and return the avatar URL (blocking).

    Args:
        username: The Twitter handle without the ``@``.

    Returns:
        The avatar URL, or ``None`` if the page has none.
    """
    response = niquests.get(
        f"{NITTER_INSTANCE}/{username}",
        timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (compatible; e.lovinator.space)"},
    )
    response.raise_for_status()
    return avatar_from_profile(response.text)


async def avatar_url_for(username: str) -> str | None:
    """Return the profile image URL for a handle, cached for a day.

    Args:
        username: The Twitter handle without the ``@``.

    Returns:
        The absolute avatar URL, or ``None`` if it cannot be resolved.
    """
    now = time.monotonic()
    cached = _avatar_cache.get(username)
    if cached is not None and now - cached[0] < _AVATAR_CACHE_TTL:
        return cached[1]

    url = None
    try:
        url = await to_thread.run_sync(_fetch_profile, username)
    except (niquests.RequestException, OSError) as exc:
        logger.warning("Could not fetch avatar for @{}: {}", username, exc)
    _avatar_cache[username] = (now, url)
    return url


def original_image_url(url: str) -> str | None:
    """Return the original ``pbs.twimg.com`` URL for a Nitter media URL.

    Nitter proxies Twitter media and encodes the original path into the proxy
    URL: percent-encoded after ``/pic/orig/media%2F`` on regular instances, or
    base64-encoded after ``/pic/enc/`` on encrypted instances. Embedding the
    reconstructed CDN URL lets clients such as Discord download the image
    directly from Twitter instead of waiting for this service to archive it.

    Args:
        url: The media URL from gallery-dl's Nitter extractor.

    Returns:
        The original Twitter CDN URL, or ``None`` when it cannot be derived.
    """
    if url.startswith("https://pbs.twimg.com/"):
        return url

    path = urlsplit(url).path
    if "/pic/" not in path:
        return None

    if "/enc/" in path:
        encoded = path.rpartition("/")[2]
        try:
            decoded = binascii.a2b_base64(encoded).decode("utf-8", "replace")
        except binascii.Error, ValueError:
            return None
        if decoded.startswith("http"):
            return decoded
        if "/" in decoded:
            return f"https://pbs.twimg.com/{decoded.lstrip('/')}"
        return f"https://pbs.twimg.com/media/{decoded}"

    name = unquote(path[match.end() :]) if (match := re.search(r"%2[fF]", path)) else path.rpartition("/")[2]
    if not name:
        return None
    return f"https://pbs.twimg.com/media/{name}"


def original_image_urls(media_items: list[dict[str, Any]]) -> list[str] | None:
    """Reconstruct the original CDN URLs for all image media items.

    Video items are skipped: their URLs cannot be mapped back to Twitter's
    CDN and are handled by the video paths.

    Args:
        media_items: The media items extracted from gallery-dl.

    Returns:
        The original URLs for every image in order, or ``None`` if any image
        cannot be mapped (the caller should fall back to downloading).
    """
    urls: list[str] = []
    for item in media_items:
        extension = (item.get("extension") or "").lower().lstrip(".")
        if not CONTENT_TYPES.get(extension, "").startswith("image/"):
            continue
        original = original_image_url(item.get("url") or "")
        if original is None:
            return None
        urls.append(original)
    return urls


def _file_sort_key(path: Path) -> tuple[int, str]:
    """Return a sort key that orders ``1.mp4`` before ``10.mp4``.

    Args:
        path: The media file path.

    Returns:
        A tuple of the numeric prefix and the filename.
    """
    match = re.match(r"^(\d+)", path.stem)
    if match:
        return (int(match.group(1)), path.name)
    return (10**9, path.name)


def list_media_files(directory: Path) -> list[Path]:
    """List the media files in a download directory in original order.

    Args:
        directory: The directory gallery-dl downloaded into.

    Returns:
        The media files sorted by their numeric prefix.
    """
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.iterdir() if is_media_file(path)),
        key=_file_sort_key,
    )


def public_url(path: Path, base_url: str, media_root: Path = TWITTER_MEDIA_DIR) -> str:
    """Build the public URL for a downloaded media file.

    Args:
        path: The local path of the media file.
        base_url: The public base URL of this service.
        media_root: The directory the media route serves.

    Returns:
        The absolute public URL for the file.
    """
    try:
        relative = path.resolve().relative_to(media_root.resolve())
    except ValueError:
        relative = Path(path.name)

    encoded = "/".join(quote(part) for part in relative.parts)
    return f"{base_url.rstrip('/')}/media/{encoded}"


def extract_data(
    job_data: list[Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extract tweet metadata and media from DataJob output.

    Args:
        job_data: The ``data`` list populated by ``gallery_dl.job.DataJob``.

    Returns:
        A tuple of the tweet metadata dictionary and the list of media items.

    Raises:
        RuntimeError: If the extractor returned no tweet metadata.
    """
    try:
        meta = next(item[1] for item in job_data if item[0] == Message.Directory)
    except StopIteration as exc:
        msg = "Extractor returned no tweet metadata"
        raise RuntimeError(msg) from exc

    media_items = [{**item[2], "url": item[1]} for item in job_data if item[0] == Message.Url]

    return meta, media_items


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    """Archive tweet metadata as JSON on disk.

    Args:
        path: Path of the JSON file to write.
        metadata: The tweet metadata to archive.
    """
    content = json.dumps(
        metadata,
        default=str,
        indent=2,
        ensure_ascii=False,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def download_directory(download_job: job.DownloadJob) -> Path | None:
    """Return the directory gallery-dl downloaded into.

    Args:
        download_job: The completed download job.

    Returns:
        The download directory, or ``None`` if no directory was set.
    """
    pathfmt = download_job.pathfmt
    if pathfmt is None or not pathfmt.directory:
        return None

    # gallery-dl's PathFormat is untyped, so Pylance infers ``directory`` as a
    # broad union of types. It is always a string path at runtime.
    return Path(str(pathfmt.directory))


_DIMENSIONS_RE = re.compile(r"(\d+)x(\d+)")


def _direct_video_url(media_items: list[dict[str, Any]]) -> str | None:
    """Return the direct MP4 URL from the media items, if present.

    gallery-dl already exposes Nitter's direct MP4 links (which carry the
    resolution in their path). HLS and yt-dlp URLs are prefixed with ``ytdl:``
    and cannot be embedded directly.

    Args:
        media_items: The media items extracted from gallery-dl.

    Returns:
        The direct MP4 URL, or ``None`` if the video needs yt-dlp.
    """
    for item in media_items:
        url = item.get("url") or ""
        if item.get("extension") == "mp4" and not url.startswith("ytdl:"):
            return url
    return None


def _video_dimensions(url: str) -> tuple[int | None, int | None]:
    """Extract the ``{width}x{height}`` resolution from a direct MP4 URL.

    Args:
        url: The direct MP4 URL.

    Returns:
        A tuple of the width and height, or ``(None, None)`` when the URL does
        not include a resolution.
    """
    if match := _DIMENSIONS_RE.search(url):
        return int(match.group(1)), int(match.group(2))
    return None, None


def fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Fetch a tweet's metadata without downloading its media.

    Args:
        nitter_url: URL of the tweet on a Nitter instance.

    Returns:
        A tuple of the tweet metadata and the list of media items, or ``None``
        if gallery-dl returned nothing.
    """
    configure_extractor()

    data_job = job.DataJob(nitter_url, file=None)
    data_job.run()

    if data_job.exception is not None:
        logger.warning("gallery-dl failed to extract {}: {}", nitter_url, data_job.exception)
        return None

    if not data_job.data:
        logger.warning("gallery-dl returned no data for {}", nitter_url)
        return None

    return extract_data(data_job.data)


def download_media(nitter_url: str) -> tuple[Path | None, list[Path]]:
    """Download a tweet's media via gallery-dl (including yt-dlp for video).

    Args:
        nitter_url: URL of the tweet on a Nitter instance.

    Returns:
        A tuple of the download directory and the downloaded media files.
    """
    download_job = job.DownloadJob(nitter_url)
    status = download_job.run()

    if status:
        logger.warning("gallery-dl finished with status {} for {}", status, nitter_url)

    directory = download_directory(download_job)
    files = list_media_files(directory) if directory is not None else []
    return directory, files


def archive(
    meta: dict[str, Any],
    media_items: list[dict[str, Any]],
    directory: Path | None,
    files: list[Path],
) -> None:
    """Write the tweet metadata to disk for later reading."""
    meta["media"] = [{key: item.get(key) for key in ("url", "num", "filename", "extension")} for item in media_items]
    meta["files"] = [
        {
            "filename": path.name,
            "path": str(path.resolve()),
            "content_type": content_type_for(path),
        }
        for path in files
    ]

    if directory is not None:
        write_metadata(directory / "metadata.json", meta)


def download(nitter_url: str) -> tuple[dict[str, Any], list[Path]] | None:
    """Download a tweet's media and archive its metadata (blocking).

    Args:
        nitter_url: URL of the tweet on a Nitter instance.

    Returns:
        A tuple of the tweet metadata and the list of downloaded media file
        paths, or ``None`` if nothing was returned.
    """
    result = fetch_meta(nitter_url)
    if result is None:
        return None

    meta, media_items = result
    directory, files = download_media(nitter_url)
    archive(meta, media_items, directory, files)
    return meta, files


def download_background(
    nitter_url: str,
    meta: dict[str, Any],
    media_items: list[dict[str, Any]],
) -> None:
    """Download a tweet's media in the background and archive its metadata.

    Args:
        nitter_url: URL of the tweet on a Nitter instance.
        meta: The tweet metadata.
        media_items: The media items extracted from gallery-dl.
    """
    directory, files = download_media(nitter_url)
    archive(meta, media_items, directory, files)


async def fetch_meta_async(
    nitter_url: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Fetch tweet metadata without blocking the event loop.

    Args:
        nitter_url: URL of the tweet on a Nitter instance.

    Returns:
        The same result as :func:`fetch_meta`.
    """
    return await to_thread.run_sync(fetch_meta, nitter_url)


async def fetch_meta_cached(
    nitter_url: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Fetch tweet metadata with a short in-memory cache.

    Args:
        nitter_url: URL of the tweet on a Nitter instance.

    Returns:
        The same result as :func:`fetch_meta_async`.
    """
    now = time.monotonic()
    cached = _meta_cache.get(nitter_url)
    if cached is not None and now - cached[0] < _META_CACHE_TTL:
        return cached[1]

    result = await fetch_meta_async(nitter_url)
    _meta_cache[nitter_url] = (now, result)
    return result


def build_embed(  # ruff: ignore[too-many-arguments]
    meta: dict[str, Any],
    files: list[Path],
    *,
    base_url: str,
    canonical_url: str,
    media_root: Path = TWITTER_MEDIA_DIR,
    video_url: str | None = None,
    width: int | None = None,
    height: int | None = None,
    image_urls: tuple[str, ...] | None = None,
) -> Embed:
    """Build an embed from tweet metadata and downloaded files.

    Args:
        meta: The tweet metadata from gallery-dl.
        files: The downloaded media files.
        base_url: The public base URL of this service.
        canonical_url: The canonical URL of the original tweet.
        media_root: The directory the media route serves.
        video_url: An external video URL to embed directly instead of a locally
            downloaded file.
        width: The pixel width of the external video.
        height: The pixel height of the external video.
        image_urls: External image URLs to embed directly instead of locally
            downloaded files.

    Returns:
        An embed ready to render as Open Graph HTML.
    """
    author = meta.get("author") or meta.get("user") or {}
    name = str(author.get("name") or "").strip() or "Twitter"
    username = str(author.get("nick") or "").strip()

    title = f"{name} ({username})" if username else name
    handle = f"@{username.lstrip('@')}" if username else None

    stats: tuple[tuple[str, str], ...] = ()
    if (retweets := meta.get("retweets")) is not None:
        stats += (("Retweets", compact_number(retweets)),)
    if (likes := meta.get("likes")) is not None:
        stats += (("Likes", compact_number(likes)),)

    description = HTMLParser(str(meta.get("content") or "")).text(
        separator=" ",
        strip=True,
    )

    if video_url is not None:
        media = (Media(url=video_url, content_type="video/mp4", width=width, height=height),)
    elif image_urls is not None:
        media = tuple(
            Media(
                url=image_url,
                content_type=content_type_for(Path(urlsplit(image_url).path).name),
            )
            for image_url in image_urls
        )
    else:
        media = tuple(
            Media(
                url=public_url(path, base_url, media_root),
                content_type=content_type_for(path),
            )
            for path in files
        )

    return Embed(
        title=title,
        description=description,
        url=canonical_url,
        media=media,
        site=handle,
        creator=handle,
        stats=stats,
    )


def generate_html(embed: Embed) -> str:
    """Render an Open Graph embed page for a tweet.

    Args:
        embed: The embed data to render.

    Returns:
        A complete HTML document.
    """
    videos = [media for media in embed.media if media.is_video]
    images = [media for media in embed.media if not media.is_video]

    tags = [
        meta(name="theme-color", content="#1d9bf0"),
        meta(property="og:type", content="video.other" if videos else "article"),
        meta(property="og:site_name", content="e.lovinator.space"),
        meta(property="og:title", content=embed.title),
        meta(property="og:description", content=embed.description),
        meta(property="og:url", content=embed.url),
        meta(
            name="twitter:card",
            content="player" if videos else "summary_large_image",
        ),
        meta(name="twitter:title", content=embed.title),
        meta(name="twitter:description", content=embed.description),
    ]

    if embed.site:
        tags.append(meta(name="twitter:site", content=embed.site))
    if embed.creator:
        tags.append(meta(name="twitter:creator", content=embed.creator))

    # Discord renders the first two pairs as fields in the embed.
    for index, (label, value) in enumerate(embed.stats[:2], 1):
        tags.extend(
            [
                meta(name=f"twitter:label{index}", content=label),
                meta(name=f"twitter:data{index}", content=value),
            ],
        )

    for image in images:
        tags.extend(
            [
                meta(property="og:image", content=image.url),
                meta(property="og:image:url", content=image.url),
                meta(property="og:image:secure_url", content=image.url),
                meta(name="twitter:image", content=image.url),
            ],
        )

    if videos:
        video = videos[0]
        width = video.width or 1280
        height = video.height or 720
        tags.extend(
            [
                meta(property="og:video", content=video.url),
                meta(property="og:video:url", content=video.url),
                meta(property="og:video:secure_url", content=video.url),
                meta(property="og:video:type", content=video.content_type),
                meta(property="og:video:width", content=str(width)),
                meta(property="og:video:height", content=str(height)),
                meta(name="twitter:player:stream", content=video.url),
                meta(
                    name="twitter:player:stream:content_type",
                    content=video.content_type,
                ),
                meta(name="twitter:player:width", content=str(width)),
                meta(name="twitter:player:height", content=str(height)),
            ],
        )

        poster = embed.poster or (images[0].url if images else None)
        if poster:
            tags.extend(
                [
                    meta(property="og:image", content=poster),
                    meta(property="og:image:secure_url", content=poster),
                    meta(name="twitter:image", content=poster),
                ],
            )

    return str(
        html[
            head[
                meta(name="viewport", content="width=device-width, initial-scale=1.0"),
                *tags,
                link(rel="icon", href="/favicon.ico"),
                link(rel="apple-touch-icon", href="/apple-touch-icon.png"),
            ],
        ],
    )


def generate_activity_html(
    embed: Embed,
    *,
    activity_url: str,
    oembed_url: str,
    avatar_url: str | None = None,
) -> str:
    """Render a Mastodon-style embed page for Discord.

    Discord renders embeds with an author row, engagement counts and a player
    only for pages that look like Mastodon statuses: the ``application/
    activity+json`` and ``application/json+oembed`` alternate links make
    Discord fetch the activity and oEmbed documents, which carry the avatar,
    counts and media. The Open Graph tags stay minimal — notably *no*
    ``og:image`` when the post has media — because an ``og:image`` makes
    Discord fall back to the plain card and lose the author row.

    Args:
        embed: The embed data to render.
        activity_url: Absolute URL of the Mastodon-style status document.
        oembed_url: Absolute URL of the oEmbed document.
        avatar_url: The author's avatar, used as the image for text-only posts.

    Returns:
        A complete HTML document.
    """
    videos = [media for media in embed.media if media.is_video]
    images = [media for media in embed.media if not media.is_video]

    tags = [
        meta(name="theme-color", content="#1d9bf0"),
        meta(property="og:site_name", content="e.lovinator.space"),
        meta(property="og:title", content=embed.title),
        meta(property="og:url", content=embed.url),
        meta(property="og:description", content=embed.description),
        meta(property="og:type", content="video.other" if videos else "article"),
        meta(property="twitter:title", content=embed.title),
    ]

    if embed.site:
        tags.append(meta(property="twitter:site", content=embed.site))
    if embed.creator:
        tags.append(meta(property="twitter:creator", content=embed.creator))

    if videos:
        video = videos[0]
        width = video.width or 1280
        height = video.height or 720
        tags.extend(
            [
                meta(property="og:video", content=video.url),
                meta(property="og:video:type", content=video.content_type),
                meta(property="og:video:width", content=str(width)),
                meta(property="og:video:height", content=str(height)),
                meta(property="twitter:player", content=video.url),
                meta(property="twitter:player:stream", content=video.url),
                meta(
                    property="twitter:player:stream:content_type",
                    content=video.content_type,
                ),
                meta(property="twitter:player:width", content=str(width)),
                meta(property="twitter:player:height", content=str(height)),
            ],
        )
    elif images:
        # Image posts: no og:image, so Discord keeps the activity card.
        tags.append(meta(name="twitter:card", content="summary_large_image"))
    elif avatar_url:
        # Text-only posts: the avatar acts as the embed image.
        tags.extend(
            [
                meta(property="og:image", content=avatar_url),
                meta(name="twitter:card", content="summary"),
            ],
        )

    return str(
        html[
            head[
                meta(name="viewport", content="width=device-width, initial-scale=1.0"),
                *tags,
                link(rel="alternate", type="application/activity+json", href=activity_url),
                link(rel="alternate", type="application/json+oembed", href=oembed_url),
                link(rel="icon", href="/favicon.ico"),
                link(rel="apple-touch-icon", href="/apple-touch-icon.png"),
            ],
        ],
    )


def _media_attachments(media_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build Mastodon ``MediaAttachment`` documents from the media items.

    Args:
        media_items: The media items extracted from gallery-dl.

    Returns:
        The attachment documents; video items keep their direct MP4 URL and
        image items are mapped back to Twitter's CDN when possible.
    """
    attachments: list[dict[str, Any]] = []
    for item in media_items:
        extension = (item.get("extension") or "").lower().lstrip(".")
        url = item.get("url") or ""
        if url.startswith("ytdl:"):
            continue
        if CONTENT_TYPES.get(extension, "").startswith("image/"):
            original = original_image_url(url) or url
            attachments.append({"type": "image", "url": original, "preview_url": original})
        elif extension == "mp4":
            attachments.append({"type": "video", "url": url})
    return attachments


def _account_document(meta: dict[str, Any], username: str, avatar: str | None) -> dict[str, Any]:
    """Build a Mastodon ``Account`` document for a tweet's author.

    Args:
        meta: The tweet metadata from gallery-dl.
        username: The canonical handle without the ``@``.
        avatar: The resolved profile image URL, if any.

    Returns:
        The Account document.
    """
    author = meta.get("author") or meta.get("user") or {}
    name = str(author.get("name") or "").strip() or "Twitter"
    return {
        "id": username,
        "username": username,
        "acct": username,
        "display_name": name,
        "locked": False,
        "bot": False,
        "discoverable": True,
        "group": False,
        "created_at": "1970-01-01T00:00:00.000Z",
        "note": "",
        "url": f"https://twitter.com/{username}",
        "avatar": avatar or "",
        "avatar_static": avatar or "",
        "header": "",
        "header_static": "",
        "followers_count": 0,
        "following_count": 0,
        "statuses_count": 0,
        "last_status_at": None,
    }


def _created_at(meta: dict[str, Any]) -> str:
    """Return the tweet's creation time as an ISO-8601 string.

    Args:
        meta: The tweet metadata from gallery-dl.

    Returns:
        The ISO-8601 timestamp, or an empty string when unknown.
    """
    created_at = meta.get("date")
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            return created_at.isoformat() + "Z"
        return created_at.isoformat()
    return str(created_at or "")


def _status_content(meta: dict[str, Any]) -> str:
    """Build the Mastodon ``content`` HTML for a tweet.

    Engagement counts are prepended in a bold paragraph, which Discord renders
    with its own emoji artwork.

    Args:
        meta: The tweet metadata from gallery-dl.

    Returns:
        The content HTML.
    """
    text = str(meta.get("content") or "")
    counts = engagement_text(
        comments=meta.get("comments"),
        retweets=meta.get("retweets"),
        likes=meta.get("likes"),
    )
    if counts:
        return f"<p><b>{counts}</b></p><p>{text}</p>"
    return f"<p>{text}</p>"


def _tweet_handle(meta: dict[str, Any], fallback: str) -> str:
    """Return the canonical handle for a tweet's author.

    Args:
        meta: The tweet metadata from gallery-dl.
        fallback: Handle from the request URL, used when metadata is missing.

    Returns:
        The handle without the ``@``.
    """
    author = meta.get("author") or meta.get("user") or {}
    nick = str(author.get("nick") or "").strip().lstrip("@")
    return nick or fallback


def client_ip_from(request: Request) -> IPv4Address | IPv6Address | None:
    """Extract the client IP address from a request.

    Args:
        request: The incoming request.

    Returns:
        The client IP address, or ``None`` if it is missing or invalid.
    """
    client = request.client
    if client is None:
        return None

    try:
        return ip_address(client.host)
    except ValueError:
        return None


def is_discord_ip(
    client_ip: IPv4Address | IPv6Address,
    ips: DiscordIPs,
) -> bool:
    """Check whether an IP address belongs to Discord.

    Args:
        client_ip: The client IP address.
        ips: The Discord IP ranges.

    Returns:
        ``True`` if the IP is within one of the Discord ranges.
    """
    return any(isinstance(client_ip, IPv4Address) and client_ip in prefix.ipv4_prefix for prefix in ips.prefixes)


async def is_discord_client(
    client_ip: IPv4Address | IPv6Address,
) -> bool:
    """Check whether a client IP belongs to Discord (or localhost).

    Args:
        client_ip: The client IP address.

    Returns:
        ``True`` if the client is Discord or the loopback address.
    """
    if client_ip.is_loopback:
        return True

    ips = await get_discord_ips()
    return is_discord_ip(client_ip, ips)


def base_url_for(request: Request) -> str:
    """Return the public base URL for the current request.

    Args:
        request: The incoming request.

    Returns:
        The scheme and host of the request without a trailing slash.
    """
    return str(request.base_url).rstrip("/")


@get("/{username:str}/status/{tweet_id:str}")
async def twitter(
    request: Request,
    username: Annotated[str, PathParameter()],
    tweet_id: Annotated[str, PathParameter()],
) -> Response | Redirect:
    """Serve an Open Graph embed for a tweet to Discord clients.

    Non-Discord clients are redirected to the original tweet on X.

    Args:
        request: The incoming request.
        username: The Twitter/X handle.
        tweet_id: The status ID of the tweet.

    Returns:
        A redirect for non-Discord clients, or an HTML embed page.
    """
    return await _twitter(request, username, tweet_id)


@get("/{username:str}/status/{tweet_id:str}/en")
async def twitter_en(
    request: Request,
    username: Annotated[str, PathParameter()],
    tweet_id: Annotated[str, PathParameter()],
) -> Response | Redirect:
    """Serve an English-translated Open Graph embed for a tweet.

    Like :func:`twitter`, but the tweet text is translated into English with
    DeepSeek before the embed is rendered.

    Args:
        request: The incoming request.
        username: The Twitter/X handle.
        tweet_id: The status ID of the tweet.

    Returns:
        A redirect for non-Discord clients, or an HTML embed page.
    """
    return await _twitter(request, username, tweet_id, translate=True)


async def _twitter(  # ruff: ignore[too-many-locals]
    request: Request,
    username: str,
    tweet_id: str,
    *,
    translate: bool = False,
) -> Response | Redirect:
    """Serve an Open Graph embed for a tweet to Discord clients.

    Non-Discord clients are redirected to the original tweet on X.

    Args:
        request: The incoming request.
        username: The Twitter/X handle.
        tweet_id: The status ID of the tweet.
        translate: Whether to translate the tweet text into English.

    Returns:
        A redirect for non-Discord clients, or an HTML embed page.

    Raises:
        ValueError: If the client address is missing.
    """
    logger.info("Request for {} from {}", request.url, request.client)

    client_ip = client_ip_from(request)
    if client_ip is None:
        msg = "No client address"
        raise ValueError(msg)

    if not await is_discord_client(client_ip):
        return Redirect(
            path=f"{ORIGINAL_URL}/{username}/status/{tweet_id}",
            status_code=302,
        )

    canonical_url = f"{ORIGINAL_URL}/{username}/status/{tweet_id}"
    nitter_url = f"{NITTER_INSTANCE}/{username}/status/{tweet_id}"
    logger.info("Fetching tweet from Nitter: {}", nitter_url)

    result = await fetch_meta_cached(nitter_url)

    if result is None:
        logger.warning("No data returned for {}", nitter_url)
        embed = Embed(
            title=f"@{username}",
            description="",
            url=canonical_url,
            media=(),
        )
        background = None
        handle = username
        avatar = None
        activity_url = None
        oembed_url = None
    else:
        meta, media_items = result
        background = None
        handle = _tweet_handle(meta, username)
        base_url = base_url_for(request)
        avatar = await avatar_url_for(handle)
        activity_url = f"{base_url}/users/{quote(handle)}/statuses/{tweet_id}"
        oembed_url = f"{base_url}/_oembed/{quote(handle)}/{tweet_id}"

        if video_url := _direct_video_url(media_items):
            width, height = _video_dimensions(video_url)
            embed = build_embed(
                meta,
                [],
                base_url=base_url,
                canonical_url=canonical_url,
                video_url=video_url,
                width=width,
                height=height,
            )
            background = BackgroundTask(download_background, nitter_url, meta, media_items)
        elif image_urls := original_image_urls(media_items):
            # Embed the images straight from Twitter's CDN so Discord does not
            # have to wait for the archive download; archive in the background.
            embed = build_embed(
                meta,
                [],
                base_url=base_url,
                canonical_url=canonical_url,
                image_urls=tuple(image_urls),
            )
            background = BackgroundTask(download_background, nitter_url, meta, media_items)
        else:
            directory, files = await to_thread.run_sync(download_media, nitter_url)
            archive(meta, media_items, directory, files)
            embed = build_embed(
                meta,
                files,
                base_url=base_url,
                canonical_url=canonical_url,
            )

    if translate:
        embed = await translate_embed(embed, ("description",))

    if activity_url is not None and oembed_url is not None:
        content = generate_activity_html(
            embed,
            activity_url=activity_url,
            oembed_url=oembed_url,
            avatar_url=avatar,
        )
    else:
        content = generate_html(embed)

    return Response(
        content=content,
        media_type="text/html",
        background=background,
    )


@get("/users/{username:str}/statuses/{tweet_id:str}")
async def users_statuses(
    request: Request,
    username: Annotated[str, PathParameter()],
    tweet_id: Annotated[str, PathParameter()],
) -> Response:
    """Serve a Mastodon-style ``Status`` document for a tweet.

    Discord follows the ``application/activity+json`` alternate link on the
    embed page to this endpoint and reads the author row, engagement counts
    and media from the returned document.

    Args:
        request: The incoming request.
        username: The Twitter/X handle.
        tweet_id: The status ID of the tweet.

    Returns:
        The Status document as JSON.
    """
    nitter_url = f"{NITTER_INSTANCE}/{username}/status/{tweet_id}"
    result = await fetch_meta_cached(nitter_url)
    if result is None:
        return Response(
            content=json.dumps({"error": "Not Found"}),
            media_type="application/json",
            status_code=404,
        )

    meta, media_items = result
    handle = _tweet_handle(meta, username)
    avatar = await avatar_url_for(handle)

    payload = status_payload(
        status_id=tweet_id,
        url=f"{ORIGINAL_URL}/{handle}/status/{tweet_id}",
        created_at=_created_at(meta),
        content=_status_content(meta),
        account=_account_document(meta, handle, avatar),
        media=_media_attachments(media_items),
        replies_count=meta.get("comments"),
        reblogs_count=meta.get("retweets"),
        favourites_count=meta.get("likes"),
    )

    return Response(
        content=json.dumps(payload),
        media_type="application/activity+json",
    )


@get("/_oembed/{username:str}/{tweet_id:str}")
async def tweet_oembed(
    request: Request,
    username: Annotated[str, PathParameter()],
    tweet_id: Annotated[str, PathParameter()],
) -> Response:
    """Serve an oEmbed document for a tweet.

    Discord reads ``author_name`` from this document for the small line above
    the embed title.

    Args:
        request: The incoming request.
        username: The Twitter/X handle.
        tweet_id: The status ID of the tweet.

    Returns:
        The oEmbed document as JSON.
    """
    nitter_url = f"{NITTER_INSTANCE}/{username}/status/{tweet_id}"
    result = await fetch_meta_cached(nitter_url)
    if result is None:
        return Response(
            content=json.dumps({"error": "Not Found"}),
            media_type="application/json",
            status_code=404,
        )

    meta, _ = result
    handle = _tweet_handle(meta, username)

    counts = engagement_text(
        comments=meta.get("comments"),
        retweets=meta.get("retweets"),
        likes=meta.get("likes"),
    )
    payload = oembed_payload(
        author_name=counts or DEFAULT_AUTHOR_TEXT,
        author_url=f"{ORIGINAL_URL}/{handle}/status/{tweet_id}",
        provider_url=base_url_for(request),
    )

    return Response(
        content=json.dumps(payload),
        media_type="application/json",
    )
