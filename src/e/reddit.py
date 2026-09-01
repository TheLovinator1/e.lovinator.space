from __future__ import annotations

import html
import json
import os
from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from urllib.parse import urlsplit

from anyio import to_thread
from dotenv import load_dotenv
from gallery_dl import config
from gallery_dl import job
from litestar import Request
from litestar import get
from litestar.params import PathParameter
from litestar.response import Redirect
from litestar.response import Template
from loguru import logger
from platformdirs import PlatformDirs

from e.twitter import format_count
from e.twitter import get_client_ip
from e.twitter import is_discord_client

if TYPE_CHECKING:
    from ipaddress import IPv4Address
    from ipaddress import IPv6Address
    from pathlib import Path

load_dotenv()


def data_dir() -> Path:
    """Return the application data directory."""
    return PlatformDirs(appauthor="TheLovinator", appname="e", ensure_exists=True, roaming=True).user_data_path


def _hosted_url(url: object) -> str | None:
    """Return a normalized Reddit-hosted media URL, if present."""
    if not isinstance(url, str):
        return None

    normalized: str = html.unescape(url)
    hostname: str | None = urlsplit(normalized).hostname
    if hostname and hostname.lower().endswith("redd.it"):
        return normalized
    return None


def configure_extractor() -> None:
    """Configure gallery-dl's Reddit extractor to use the configured OAuth client."""
    config.set(("extractor", "reddit"), key="client-id", value="yH0aTnJEt6qUgGn835B4vg")
    config.set(("extractor", "reddit"), key="user-agent", value="org.quantumbadger.redreader/1.25.1")
    config.set(("extractor", "reddit"), key="refresh-token", value=os.getenv("REDDIT_REFRESH_TOKEN", ""))


def reddit_downloads_path(subreddit: str) -> Path:
    """Return the directory where a subreddit's post metadata is cached."""
    reddit_download_path: Path = data_dir() / "Reddit" / "Downloads" / subreddit
    reddit_download_path.mkdir(parents=True, exist_ok=True)
    return reddit_download_path


def get_reddit_post(reddit_url: str, subreddit: str, post_id: str) -> dict[str, Any]:
    """Read cached post metadata or fetch it through gallery-dl.

    Returns:
        The first post's data object.

    Raises:
        RuntimeError: If gallery-dl cannot extract the post.
        ValueError: If gallery-dl returns no post metadata.
    """
    cache_file: Path = reddit_downloads_path(subreddit) / f"{post_id}.json"
    if cache_file.exists():
        logger.info("Reddit post already downloaded: {}", post_id)
        try:
            cached_post: Any = json.loads(cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Cached Reddit post {} was corrupted, re-downloading", post_id)
        else:
            if isinstance(cached_post, dict):
                return cached_post

    logger.info("Downloading Reddit post: {}", post_id)
    configure_extractor()
    data_job: job.DataJob = job.DataJob(reddit_url, file=None)
    data_job.run()

    if data_job.exception is not None:
        msg: str = f"gallery-dl failed to extract {reddit_url}"
        raise RuntimeError(msg) from data_job.exception
    if not data_job.data_post:
        msg: str = f"gallery-dl returned no post metadata for {reddit_url}"
        raise ValueError(msg)

    post: dict[str, Any] = data_job.data_post[0]
    cache_file.write_text(json.dumps(post, ensure_ascii=False, default=str), encoding="utf-8")
    return post


def reddit_media(post: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str | None]:
    """Extract Reddit-hosted images, video, and its poster from a post.

    Returns:
        The images, optional progressive video data, and optional poster URL.
    """
    preview: dict[str, Any] = post.get("preview") or {}
    preview_images: list[dict[str, Any]] = preview.get("images") or []
    poster: str | None = _hosted_url((preview_images[0].get("source") or {}).get("url")) if preview_images else None

    reddit_video: dict[str, Any] = (post.get("secure_media") or {}).get("reddit_video") or {}
    if video_url := _hosted_url(reddit_video.get("fallback_url")):
        return (
            [],
            {
                "url": video_url,
                "content_type": "video/mp4",
                "width": int(reddit_video.get("width") or 1280),
                "height": int(reddit_video.get("height") or 720),
            },
            poster,
        )

    images: list[dict[str, Any]] = []
    metadata: dict[str, Any] = post.get("media_metadata") or {}
    for item in (post.get("gallery_data") or {}).get("items") or []:
        media: dict[str, Any] = metadata.get(item.get("media_id")) or {}
        source: dict[str, Any] = media.get("s") or {}
        if image_url := _hosted_url(source.get("u")):
            images.append({
                "url": image_url,
                "width": int(source.get("x") or 1280),
                "height": int(source.get("y") or 720),
            })

    if not images and poster:
        source = preview_images[0].get("source") or {}
        images.append({
            "url": poster,
            "width": int(source.get("width") or 1280),
            "height": int(source.get("height") or 720),
        })

    return images, None, poster


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
) -> Template | Redirect:
    """Serve an Open Graph Reddit embed to Discord clients.

    Returns:
        A redirect for non-Discord clients or a rendered embed page.
    """
    canonical_url: str = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}"
    client_ip: IPv4Address | IPv6Address | None = get_client_ip(request)
    if client_ip is None or not await is_discord_client(client_ip):
        return Redirect(canonical_url, status_code=302)

    try:
        post: dict[str, Any] = await to_thread.run_sync(get_reddit_post, canonical_url, subreddit, post_id)
    except (RuntimeError, ValueError) as exc:
        logger.warning("Failed to fetch Reddit post {}: {}", canonical_url, exc)
        return Redirect(canonical_url, status_code=302)

    images: list[dict[str, Any]]
    video: dict[str, Any] | None
    poster: str | None
    images, video, poster = reddit_media(post)
    title: str = str(post.get("title") or "Reddit")
    author: str = str(post.get("author") or "reddit")
    post_subreddit: str = str(post.get("subreddit") or subreddit)
    upvotes: str = format_count(int(post.get("ups") or post.get("score") or 0))
    comments: str = format_count(int(post.get("num_comments") or 0))
    summary: str = f"u/{author} in r/{post_subreddit} | {upvotes} upvotes | {comments} comments"
    selftext: str = str(post.get("selftext") or "").strip()
    description: str = f"{summary}\n\n{selftext}" if selftext else summary

    response = Template(
        template_name="reddit.html",
        context={
            "images": images,
            "video": video,
            "poster": poster,
            "og_type": "video.other" if video else "article",
            "title": title,
            "description": description,
            "canonical_url": canonical_url,
            "author": author,
            "subreddit": post_subreddit,
        },
    )
    response.headers["Cache-Control"] = "public, max-age=300"
    return response
