from __future__ import annotations

import re
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

from e.settings import REDDIT_ARCHIVE_PATH
from e.settings import REDDIT_CLIENT_ID
from e.settings import REDDIT_MEDIA_DIR
from e.settings import REDDIT_REFRESH_TOKEN
from e.settings import REDDIT_URL
from e.settings import REDDIT_USER_AGENT
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


def configure_extractor() -> None:
    """Configure gallery-dl's Reddit extractor."""
    config.set(
        path=("extractor",),
        key="base-directory",
        value=str(REDDIT_MEDIA_DIR),
    )
    config.set(
        path=("extractor", "reddit"),
        key="directory",
        value=["{subreddit}", "{id}"],
    )
    config.set(
        path=("extractor",),
        key="archive",
        value=str(REDDIT_ARCHIVE_PATH),
    )
    config.set(
        path=("extractor",),
        key="archive-pragma",
        value=["journal_mode=WAL", "synchronous=NORMAL"],
    )
    config.set(
        path=("extractor", "reddit"),
        key="client-id",
        value=REDDIT_CLIENT_ID,
    )
    config.set(
        path=("extractor", "reddit"),
        key="user-agent",
        value=REDDIT_USER_AGENT,
    )
    if REDDIT_REFRESH_TOKEN:
        config.set(
            path=("extractor", "reddit"),
            key="refresh-token",
            value=REDDIT_REFRESH_TOKEN,
        )


_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def _fallback_url(meta: dict[str, Any]) -> str | None:
    """Return the direct progressive MP4 URL for a Reddit video post.

    Args:
        meta: The post metadata from gallery-dl.

    Returns:
        The ``fallback_url`` (a single MP4 with audio), or ``None`` if the post
        is not a v.redd.it video.
    """
    reddit_video = (meta.get("secure_media") or {}).get("reddit_video") or {}
    return reddit_video.get("fallback_url")


def _video_path(meta: dict[str, Any]) -> Path:
    """Return the destination path for a video post's MP4.

    The directory mirrors gallery-dl's ``{subreddit}/{id}`` layout and the
    filename mirrors its ``{id} {title}.{extension}`` layout.

    Args:
        meta: The post metadata from gallery-dl.

    Returns:
        The absolute path the video should be stored at.
    """
    directory = REDDIT_MEDIA_DIR / str(meta.get("subreddit") or "") / str(meta.get("id") or "")
    title = _INVALID_FILENAME_CHARS.sub("_", str(meta.get("title") or "").strip())
    title = title.rstrip(". ")[:220] or "video"
    return directory / f"{meta['id']} {title}.mp4"


def _download_fallback(url: str, target: Path) -> Path:
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
        with niquests.get(url, stream=True, headers={"User-Agent": REDDIT_USER_AGENT}) as response:
            response.raise_for_status()
            with partial.open("wb") as file:
                for chunk in response.iter_content(chunk_size=65536):
                    file.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)

    return target


def fetch_meta(reddit_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Fetch a Reddit post's metadata without downloading its media.

    Args:
        reddit_url: URL of the post on Reddit.

    Returns:
        A tuple of the post metadata and the list of media items, or ``None``
        if gallery-dl returned nothing.
    """
    configure_extractor()

    data_job = job.DataJob(reddit_url, file=None)
    data_job.run()

    if data_job.exception is not None:
        logger.warning("gallery-dl failed to extract {}: {}", reddit_url, data_job.exception)
        return None

    if not data_job.data:
        logger.warning("gallery-dl returned no data for {}", reddit_url)
        return None

    return extract_data(data_job.data)


def download_media(reddit_url: str) -> tuple[Path | None, list[Path]]:
    """Download a non-video post's media via gallery-dl.

    Args:
        reddit_url: URL of the post on Reddit.

    Returns:
        A tuple of the download directory and the downloaded media files.
    """
    download_job = job.DownloadJob(reddit_url)
    status = download_job.run()

    if status:
        logger.warning("gallery-dl finished with status {} for {}", status, reddit_url)

    directory = download_directory(download_job)
    files = list_media_files(directory) if directory is not None else []
    return directory, files


def archive(
    meta: dict[str, Any],
    media_items: list[dict[str, Any]],
    directory: Path | None,
    files: list[Path],
) -> None:
    """Write the post metadata to disk for later reading."""
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


def download(reddit_url: str) -> tuple[dict[str, Any], list[Path]] | None:
    """Download a Reddit post's media and archive its metadata (blocking).

    Args:
        reddit_url: URL of the post on Reddit.

    Returns:
        A tuple of the post metadata and the list of downloaded media file
        paths, or ``None`` if nothing was returned.
    """
    result = fetch_meta(reddit_url)
    if result is None:
        return None

    meta, media_items = result

    # Reddit videos have a direct progressive MP4 (``fallback_url``) with audio
    # already muxed in. Download it straight from the CDN instead of running
    # yt-dlp/ffmpeg over the DASH manifest.
    fallback_url = _fallback_url(meta)
    if fallback_url is not None:
        try:
            target = _video_path(meta)
            files = [_download_fallback(fallback_url, target)]
        except (niquests.RequestException, OSError) as exc:
            logger.warning("Fallback video download failed for {}: {}", reddit_url, exc)
        else:
            archive(meta, media_items, target.parent, files)
            return meta, files

    directory, files = download_media(reddit_url)
    archive(meta, media_items, directory, files)
    return meta, files


def download_video_background(
    fallback_url: str,
    target: Path,
    meta: dict[str, Any],
    media_items: list[dict[str, Any]],
) -> None:
    """Download a video in the background and archive its metadata.

    Args:
        fallback_url: The direct MP4 URL to download.
        target: The destination file path.
        meta: The post metadata.
        media_items: The media items extracted from gallery-dl.
    """
    try:
        _download_fallback(fallback_url, target)
    except (niquests.RequestException, OSError) as exc:
        logger.warning("Background video download failed: {}", exc)
        return

    archive(meta, media_items, target.parent, [target])


async def fetch_meta_async(
    reddit_url: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """Fetch post metadata without blocking the event loop.

    Args:
        reddit_url: URL of the post on Reddit.

    Returns:
        The same result as :func:`fetch_meta`.
    """
    return await to_thread.run_sync(fetch_meta, reddit_url)


async def download_media_async(reddit_url: str) -> tuple[Path | None, list[Path]]:
    """Download a non-video post's media without blocking the event loop.

    Args:
        reddit_url: URL of the post on Reddit.

    Returns:
        The same result as :func:`download_media`.
    """
    return await to_thread.run_sync(download_media, reddit_url)


def build_embed(  # ruff: ignore[too-many-arguments]
    meta: dict[str, Any],
    files: list[Path],
    *,
    base_url: str,
    canonical_url: str,
    media_root: Path = REDDIT_MEDIA_DIR,
    video_url: str | None = None,
) -> Embed:
    """Build an embed from a Reddit post and downloaded files.

    Args:
        meta: The post metadata from gallery-dl.
        files: The downloaded media files.
        base_url: The public base URL of this service.
        canonical_url: The canonical URL of the original post.
        media_root: The directory the media route serves.
        video_url: An external video URL (Reddit's ``fallback_url``) to embed
            directly instead of a locally downloaded file.

    Returns:
        An embed ready to render as Open Graph HTML.
    """
    title = str(meta.get("title") or "").strip() or "Reddit"
    description = str(meta.get("selftext") or "").strip()

    stats: tuple[tuple[str, str], ...] = ()
    upvotes = meta.get("ups")
    if upvotes is None:
        upvotes = meta.get("score")
    if upvotes is not None:
        stats += (("Upvotes", compact_number(upvotes)),)
    if (num_comments := meta.get("num_comments")) is not None:
        stats += (("Comments", compact_number(num_comments)),)

    reddit_video = (meta.get("secure_media") or {}).get("reddit_video") or {}
    width = reddit_video.get("width")
    height = reddit_video.get("height")

    poster = None
    try:
        poster = meta["preview"]["images"][0]["source"]["url"]
    except KeyError, IndexError, TypeError:
        poster = meta.get("thumbnail")

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


@get(
    [
        "/r/{subreddit:str}/comments/{post_id:str}",
        "/r/{subreddit:str}/comments/{post_id:str}/{slug:str}",
    ],
)
async def reddit(
    request: Request,
    subreddit: Annotated[str, PathParameter()],
    post_id: Annotated[str, PathParameter()],
    slug: Annotated[str, PathParameter()] = "",
) -> Response | Redirect:
    """Serve an Open Graph embed for a Reddit post to Discord clients.

    Non-Discord clients are redirected to the original post on Reddit.

    Args:
        request: The incoming request.
        subreddit: The subreddit the post belongs to.
        post_id: The ID of the post.
        slug: The optional post slug. Accepted for compatibility with Reddit
            URLs, but ignored (post IDs are unique).

    Returns:
        A redirect for non-Discord clients, or an HTML embed page.
    """
    return await _reddit(request, subreddit, post_id, slug)


@get(
    [
        "/r/{subreddit:str}/comments/{post_id:str}/en",
        "/r/{subreddit:str}/comments/{post_id:str}/{slug:str}/en",
    ],
)
async def reddit_en(
    request: Request,
    subreddit: Annotated[str, PathParameter()],
    post_id: Annotated[str, PathParameter()],
    slug: Annotated[str, PathParameter()] = "",
) -> Response | Redirect:
    """Serve an English-translated Open Graph embed for a Reddit post.

    Like :func:`reddit`, but the post title and text are translated into
    English with DeepSeek before the embed is rendered.

    Args:
        request: The incoming request.
        subreddit: The subreddit the post belongs to.
        post_id: The ID of the post.
        slug: The optional post slug. Accepted for compatibility with Reddit
            URLs, but ignored (post IDs are unique).

    Returns:
        A redirect for non-Discord clients, or an HTML embed page.
    """
    return await _reddit(request, subreddit, post_id, slug, translate=True)


async def _reddit(
    request: Request,
    subreddit: str,
    post_id: str,
    slug: str = "",
    *,
    translate: bool = False,
) -> Response | Redirect:
    """Serve an Open Graph embed for a Reddit post to Discord clients.

    Non-Discord clients are redirected to the original post on Reddit.

    Args:
        request: The incoming request.
        subreddit: The subreddit the post belongs to.
        post_id: The ID of the post.
        slug: The optional post slug. Accepted for compatibility with Reddit
            URLs, but ignored (post IDs are unique).
        translate: Whether to translate the post text into English.

    Returns:
        A redirect for non-Discord clients, or an HTML embed page.

    Raises:
        ValueError: If the client address is missing.
    """
    canonical_url = f"{REDDIT_URL}/r/{subreddit}/comments/{post_id}"
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

    logger.info("Fetching post from Reddit: {}", canonical_url)

    result = await fetch_meta_async(canonical_url)

    if result is None:
        logger.warning("No data returned for {}", canonical_url)
        embed = Embed(
            title=f"r/{subreddit}",
            description="",
            url=canonical_url,
            media=(),
        )
        background = None
    else:
        meta, media_items = result
        background = None

        fallback_url = _fallback_url(meta)
        if fallback_url is not None:
            # Serve the embed immediately using Reddit's direct MP4, then
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
                    video_url=fallback_url,
                )
                background = BackgroundTask(download_video_background, fallback_url, target, meta, media_items)
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
