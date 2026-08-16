from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any

import niquests
from anyio import to_thread
from gallery_dl import config
from gallery_dl import job
from litestar import Request
from litestar import get
from litestar.background_tasks import BackgroundTask
from litestar.params import PathParameter
from litestar.response import Redirect
from litestar.response import Response
from loguru import logger

from e.settings import REDGIFS_ARCHIVE_PATH
from e.settings import REDGIFS_MEDIA_DIR
from e.settings import REDGIFS_URL
from e.translate import translate_embed
from e.twitter import Embed
from e.twitter import Media
from e.twitter import base_url_for
from e.twitter import client_ip_from
from e.twitter import compact_number
from e.twitter import content_type_for
from e.twitter import download_directory
from e.twitter import extract_data
from e.twitter import generate_html
from e.twitter import is_discord_client
from e.twitter import list_media_files
from e.twitter import public_url
from e.twitter import write_metadata

if TYPE_CHECKING:
    from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
"""User-Agent sent with media downloads from Redgifs' CDN."""


def configure_extractor() -> None:
    """Configure gallery-dl's Redgifs extractor."""
    config.set(
        path=("extractor",),
        key="base-directory",
        value=str(REDGIFS_MEDIA_DIR),
    )
    config.set(
        path=("extractor", "redgifs"),
        key="directory",
        value=["{category}", "{id}"],
    )
    config.set(
        path=("extractor", "redgifs"),
        key="filename",
        value="{id}.{extension}",
    )
    config.set(
        path=("extractor",),
        key="archive",
        value=str(REDGIFS_ARCHIVE_PATH),
    )
    config.set(
        path=("extractor",),
        key="archive-pragma",
        value=["journal_mode=WAL", "synchronous=NORMAL"],
    )


def _direct_video_url(media_items: list[dict[str, Any]]) -> str | None:
    """Return the direct MP4 URL from the media items, if present.

    gallery-dl exposes Redgifs' progressive MP4 (the ``hd`` format) directly,
    so no yt-dlp/ffmpeg pass is needed. HLS and yt-dlp URLs are prefixed with
    ``ytdl:`` and cannot be embedded directly.

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


def _video_path(meta: dict[str, Any]) -> Path:
    """Return the destination path for a gif's video file.

    The directory mirrors gallery-dl's ``{category}/{id}`` layout and the
    filename mirrors its ``{id}.{extension}`` layout.

    Args:
        meta: The gif metadata from gallery-dl.

    Returns:
        The absolute path the video should be stored at.
    """
    directory = REDGIFS_MEDIA_DIR / str(meta.get("category") or "redgifs") / str(meta.get("id") or "")
    return directory / f"{meta['id']}.mp4"


def _download_video(url: str, target: Path) -> Path:
    """Download ``url`` to ``target``, skipping if it already exists.

    Args:
        url: The direct MP4 URL to download.
        target: The destination file path.

    Returns:
        The destination path.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        return target

    partial = target.with_name(target.name + ".part")
    try:
        with niquests.get(url, stream=True, headers={"User-Agent": USER_AGENT}) as response:
            response.raise_for_status()
            with partial.open("wb") as file:
                for chunk in response.iter_content(chunk_size=65536):
                    file.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)

    return target


def fetch_meta(redgifs_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Fetch a gif's metadata without downloading its media.

    Args:
        redgifs_url: URL of the gif on Redgifs.

    Returns:
        A tuple of the gif metadata and the list of media items, or ``None``
        if gallery-dl returned nothing.
    """
    configure_extractor()

    data_job = job.DataJob(redgifs_url, file=None)
    data_job.run()

    if data_job.exception is not None:
        logger.warning("gallery-dl failed to extract {}: {}", redgifs_url, data_job.exception)
        return None

    if not data_job.data:
        logger.warning("gallery-dl returned no data for {}", redgifs_url)
        return None

    return extract_data(data_job.data)


def download_media(redgifs_url: str) -> tuple[Path | None, list[Path]]:
    """Download a gif's media via gallery-dl.

    Args:
        redgifs_url: URL of the gif on Redgifs.

    Returns:
        A tuple of the download directory and the downloaded media files.
    """
    download_job = job.DownloadJob(redgifs_url)
    status = download_job.run()

    if status:
        logger.warning("gallery-dl finished with status {} for {}", status, redgifs_url)

    directory = download_directory(download_job)
    files = list_media_files(directory) if directory is not None else []
    return directory, files


def archive(
    meta: dict[str, Any],
    media_items: list[dict[str, Any]],
    directory: Path | None,
    files: list[Path],
) -> None:
    """Write the gif metadata to disk for later reading."""
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


def download(redgifs_url: str) -> tuple[dict[str, Any], list[Path]] | None:
    """Download a gif's media and archive its metadata (blocking).

    Args:
        redgifs_url: URL of the gif on Redgifs.

    Returns:
        A tuple of the gif metadata and the list of downloaded media file
        paths, or ``None`` if nothing was returned.
    """
    result = fetch_meta(redgifs_url)
    if result is None:
        return None

    meta, media_items = result

    # Redgifs serves a progressive MP4 (``hd`` format) with audio already
    # muxed in. Download it straight from the CDN instead of running
    # yt-dlp/ffmpeg over HLS.
    video_url = _direct_video_url(media_items)
    if video_url is not None:
        try:
            target = _video_path(meta)
            files = [_download_video(video_url, target)]
        except (niquests.RequestException, OSError) as exc:
            logger.warning("Direct video download failed for {}: {}", redgifs_url, exc)
        else:
            archive(meta, media_items, target.parent, files)
            return meta, files

    directory, files = download_media(redgifs_url)
    archive(meta, media_items, directory, files)
    return meta, files


def download_video_background(
    video_url: str,
    target: Path,
    meta: dict[str, Any],
    media_items: list[dict[str, Any]],
) -> None:
    """Download a video in the background and archive its metadata.

    Args:
        video_url: The direct MP4 URL to download.
        target: The destination file path.
        meta: The gif metadata.
        media_items: The media items extracted from gallery-dl.
    """
    try:
        _download_video(video_url, target)
    except (niquests.RequestException, OSError) as exc:
        logger.warning("Background video download failed: {}", exc)
        return

    archive(meta, media_items, target.parent, [target])


async def fetch_meta_async(
    redgifs_url: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Fetch gif metadata without blocking the event loop.

    Args:
        redgifs_url: URL of the gif on Redgifs.

    Returns:
        The same result as :func:`fetch_meta`.
    """
    return await to_thread.run_sync(fetch_meta, redgifs_url)


async def download_media_async(redgifs_url: str) -> tuple[Path | None, list[Path]]:
    """Download a gif's media without blocking the event loop.

    Args:
        redgifs_url: URL of the gif on Redgifs.

    Returns:
        The same result as :func:`download_media`.
    """
    return await to_thread.run_sync(download_media, redgifs_url)


def build_embed(  # ruff: ignore[too-many-arguments]
    meta: dict[str, Any],
    files: list[Path],
    *,
    base_url: str,
    canonical_url: str,
    media_root: Path = REDGIFS_MEDIA_DIR,
    video_url: str | None = None,
) -> Embed:
    """Build an embed from a gif and downloaded files.

    Args:
        meta: The gif metadata from gallery-dl.
        files: The downloaded media files.
        base_url: The public base URL of this service.
        canonical_url: The canonical URL of the original gif.
        media_root: The directory the media route serves.
        video_url: An external video URL (Redgifs' ``hd`` MP4) to embed
            directly instead of a locally downloaded file.

    Returns:
        An embed ready to render as Open Graph HTML.
    """
    user_name = str(meta.get("userName") or "").strip()
    title = f"{user_name} on Redgifs" if user_name else "Redgifs"

    description = str(meta.get("description") or "").strip()
    tags = ", ".join(str(tag) for tag in (meta.get("tags") or []))
    if description and tags:
        description = f"{description}\n\nTags: {tags}"
    elif not description:
        description = tags

    stats: tuple[tuple[str, str], ...] = ()
    if (views := meta.get("views")) is not None:
        stats += (("Views", compact_number(views)),)
    if (likes := meta.get("likes")) is not None:
        stats += (("Likes", compact_number(likes)),)

    width = meta.get("width")
    height = meta.get("height")

    urls = meta.get("urls") or {}
    poster = urls.get("poster") or urls.get("thumbnail")

    if video_url is not None:
        media = (Media(url=video_url, content_type="video/mp4", width=width, height=height),)
    else:
        media = tuple(
            Media(
                url=public_url(path, base_url, media_root),
                content_type=content_type_for(path),
                width=width if content_type_for(path).startswith("video/") else None,
                height=height if content_type_for(path).startswith("video/") else None,
            )
            for path in files
        )

    return Embed(
        title=title,
        description=description,
        url=canonical_url,
        media=media,
        poster=poster,
        stats=stats,
    )


@get("/watch/{gif_id:str}")
async def redgifs(
    request: Request,
    gif_id: Annotated[str, PathParameter()],
) -> Response | Redirect:
    """Serve an Open Graph embed for a gif to Discord clients.

    Non-Discord clients are redirected to the original gif on Redgifs.

    Args:
        request: The incoming request.
        gif_id: The ID of the gif.

    Returns:
        A redirect for non-Discord clients, or an HTML embed page.
    """
    return await _redgifs(request, gif_id)


@get("/watch/{gif_id:str}/en")
async def redgifs_en(
    request: Request,
    gif_id: Annotated[str, PathParameter()],
) -> Response | Redirect:
    """Serve an English-translated Open Graph embed for a gif.

    Like :func:`redgifs`, but the gif title and description are translated
    into English with DeepSeek before the embed is rendered.

    Args:
        request: The incoming request.
        gif_id: The ID of the gif.

    Returns:
        A redirect for non-Discord clients, or an HTML embed page.
    """
    return await _redgifs(request, gif_id, translate=True)


async def _redgifs(
    request: Request,
    gif_id: str,
    *,
    translate: bool = False,
) -> Response | Redirect:
    """Serve an Open Graph embed for a gif to Discord clients.

    Non-Discord clients are redirected to the original gif on Redgifs.

    Args:
        request: The incoming request.
        gif_id: The ID of the gif.
        translate: Whether to translate the gif text into English.

    Returns:
        A redirect for non-Discord clients, or an HTML embed page.

    Raises:
        ValueError: If the client address is missing.
    """
    canonical_url = f"{REDGIFS_URL}/watch/{gif_id}"
    logger.info("Request for {} from {}", request.url, request.client)

    client_ip = client_ip_from(request)
    if client_ip is None:
        msg = "No client address"
        raise ValueError(msg)

    if not await is_discord_client(client_ip):
        return Redirect(
            path=canonical_url,
            status_code=302,
        )

    logger.info("Fetching gif from Redgifs: {}", canonical_url)

    result = await fetch_meta_async(canonical_url)

    if result is None:
        logger.warning("No data returned for {}", canonical_url)
        embed = Embed(
            title="Redgifs",
            description="",
            url=canonical_url,
            media=(),
        )
        background = None
    else:
        meta, media_items = result
        background = None

        video_url = _direct_video_url(media_items)
        if video_url is not None:
            # Serve the embed immediately using Redgifs' direct MP4, then
            # download it in the background so subsequent requests are
            # self-hosted.
            target = _video_path(meta)
            if target.is_file():
                embed = build_embed(
                    meta,
                    [target],
                    base_url=base_url_for(request),
                    canonical_url=canonical_url,
                )
            else:
                embed = build_embed(
                    meta,
                    [],
                    base_url=base_url_for(request),
                    canonical_url=canonical_url,
                    video_url=video_url,
                )
                background = BackgroundTask(download_video_background, video_url, target, meta, media_items)
        else:
            directory, files = await download_media_async(canonical_url)
            archive(meta, media_items, directory, files)
            embed = build_embed(
                meta,
                files,
                base_url=base_url_for(request),
                canonical_url=canonical_url,
            )

    if translate:
        embed = await translate_embed(embed, ("title", "description"))

    return Response(
        content=generate_html(embed),
        media_type="text/html",
        background=background,
    )
