from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any

from anyio import to_thread
from gallery_dl import config
from gallery_dl import job
from litestar import Request
from litestar import get
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
from e.twitter import Embed
from e.twitter import Media
from e.twitter import base_url_for
from e.twitter import client_ip_from
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


def download(reddit_url: str) -> tuple[dict[str, Any], list[Path]] | None:
    """Download a Reddit post's media and archive its metadata.

    Args:
        reddit_url: URL of the post on Reddit.

    Returns:
        A tuple of the post metadata and the list of downloaded media file
        paths, or ``None`` if nothing was returned.
    """
    configure_extractor()

    # Extract the post metadata without downloading anything.
    data_job = job.DataJob(reddit_url, file=None)
    data_job.run()

    if data_job.exception is not None:
        logger.warning(
            "gallery-dl failed to extract {}: {}",
            reddit_url,
            data_job.exception,
        )
        return None

    if not data_job.data:
        logger.warning("gallery-dl returned no data for {}", reddit_url)
        return None

    meta, media_items = extract_data(data_job.data)

    # Download the media files (including video conversion via yt-dlp).
    download_job = job.DownloadJob(reddit_url)
    status = download_job.run()

    if status:
        logger.warning(
            "gallery-dl finished with status {} for {}",
            status,
            reddit_url,
        )

    directory = download_directory(download_job)
    files = list_media_files(directory) if directory is not None else []

    # Archive the post data so it can be read without re-fetching Reddit.
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
    reddit_url: str,
) -> tuple[dict[str, Any], list[Path]] | None:
    """Download a Reddit post's media without blocking the event loop.

    Args:
        reddit_url: URL of the post on Reddit.

    Returns:
        The same result as :func:`download`.
    """
    return await to_thread.run_sync(download, reddit_url)


def build_embed(
    meta: dict[str, Any],
    files: list[Path],
    *,
    base_url: str,
    canonical_url: str,
    media_root: Path = REDDIT_MEDIA_DIR,
) -> Embed:
    """Build an embed from a Reddit post and downloaded files.

    Args:
        meta: The post metadata from gallery-dl.
        files: The downloaded media files.
        base_url: The public base URL of this service.
        canonical_url: The canonical URL of the original post.
        media_root: The directory the media route serves.

    Returns:
        An embed ready to render as Open Graph HTML.
    """
    title = str(meta.get("title") or "").strip() or "Reddit"
    description = str(meta.get("selftext") or "").strip()

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

    result = await download_async(canonical_url)

    if result is None:
        logger.warning("No data returned for {}", canonical_url)
        embed = Embed(
            title=f"r/{subreddit}",
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
