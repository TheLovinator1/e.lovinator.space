from __future__ import annotations

import json
import re
from dataclasses import dataclass
from ipaddress import IPv4Address
from ipaddress import IPv6Address
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated
from typing import Any
from urllib.parse import quote

from anyio import to_thread
from gallery_dl import config
from gallery_dl import job
from gallery_dl.extractor.message import Message
from htpy import head
from htpy import html
from htpy import meta
from litestar import Request
from litestar import get
from litestar.params import PathParameter
from litestar.response import Redirect
from litestar.response import Response
from loguru import logger
from selectolax.parser import HTMLParser

from e.discord import DiscordIPs
from e.discord import get_discord_ips
from e.settings import ARCHIVE_PATH
from e.settings import MEDIA_DIR
from e.settings import NITTER_INSTANCE
from e.settings import ORIGINAL_URL

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


def configure_extractor() -> None:
    """Configure gallery-dl's Nitter extractor."""
    config.set(
        path=("extractor",),
        key="base-directory",
        value=str(MEDIA_DIR),
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


def public_url(path: Path, base_url: str, media_root: Path = MEDIA_DIR) -> str:
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


def download(nitter_url: str) -> tuple[dict[str, Any], list[Path]] | None:
    """Download a tweet's media and archive its metadata.

    Args:
        nitter_url: URL of the tweet on a Nitter instance.

    Returns:
        A tuple of the tweet metadata and the list of downloaded media file
        paths, or ``None`` if nothing was returned.
    """
    configure_extractor()

    # Extract the tweet metadata without downloading anything.
    data_job = job.DataJob(nitter_url, file=None)
    data_job.run()

    if data_job.exception is not None:
        logger.warning(
            "gallery-dl failed to extract {}: {}",
            nitter_url,
            data_job.exception,
        )
        return None

    if not data_job.data:
        logger.warning("gallery-dl returned no data for {}", nitter_url)
        return None

    meta, media_items = extract_data(data_job.data)

    # Download the media files (including video conversion via yt-dlp).
    download_job = job.DownloadJob(nitter_url)
    status = download_job.run()

    if status:
        logger.warning(
            "gallery-dl finished with status {} for {}",
            status,
            nitter_url,
        )

    directory = download_directory(download_job)
    files = list_media_files(directory) if directory is not None else []

    # Archive the tweet data so it can be read without re-fetching Nitter.
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

    return meta, files


async def download_async(
    nitter_url: str,
) -> tuple[dict[str, Any], list[Path]] | None:
    """Download a tweet's media without blocking the event loop.

    Args:
        nitter_url: URL of the tweet on a Nitter instance.

    Returns:
        The same result as :func:`download`.
    """
    return await to_thread.run_sync(download, nitter_url)


def build_embed(
    meta: dict[str, Any],
    files: list[Path],
    *,
    base_url: str,
    canonical_url: str,
    media_root: Path = MEDIA_DIR,
) -> Embed:
    """Build an embed from tweet metadata and downloaded files.

    Args:
        meta: The tweet metadata from gallery-dl.
        files: The downloaded media files.
        base_url: The public base URL of this service.
        canonical_url: The canonical URL of the original tweet.
        media_root: The directory the media route serves.

    Returns:
        An embed ready to render as Open Graph HTML.
    """
    author = meta.get("author") or meta.get("user") or {}
    name = str(author.get("name") or "").strip() or "Twitter"
    username = str(author.get("nick") or "").strip()

    title = f"{name} ({username})" if username else name

    description = HTMLParser(str(meta.get("content") or "")).text(
        separator=" ",
        strip=True,
    )

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

    for image in images:
        tags.extend(
            [
                meta(property="og:image", content=image.url),
                meta(property="og:image:url", content=image.url),
                meta(property="og:image:secure_url", content=image.url),
                meta(name="twitter:image", content=image.url),
            ],
        )

    for video in videos:
        tags.extend(
            [
                meta(property="og:video", content=video.url),
                meta(property="og:video:url", content=video.url),
                meta(property="og:video:secure_url", content=video.url),
                meta(property="og:video:type", content=video.content_type),
                meta(name="twitter:player", content=video.url),
                meta(name="twitter:player:stream", content=video.url),
                meta(
                    name="twitter:player:stream:content_type",
                    content=video.content_type,
                ),
            ],
        )

    # Use the first image as the poster for video embeds.
    if videos and images:
        tags.extend(
            [
                meta(property="og:image", content=images[0].url),
                meta(name="twitter:image", content=images[0].url),
            ],
        )

    return str(
        html[
            head[
                meta(name="viewport", content="width=device-width, initial-scale=1.0"),
                *tags,
            ],
        ],
    )


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

    result = await download_async(nitter_url)

    if result is None:
        logger.warning("No data returned for {}", nitter_url)
        embed = Embed(
            title=f"@{username}",
            description="",
            url=canonical_url,
            media=(),
        )
    else:
        meta, files = result
        embed = build_embed(
            meta,
            files,
            base_url=base_url_for(request),
            canonical_url=canonical_url,
        )

    return Response(
        content=generate_html(embed),
        media_type="text/html",
    )
