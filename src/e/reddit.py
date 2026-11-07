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
from litestar.exceptions import NotFoundException
from litestar.params import PathParameter
from litestar.response import File
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
    if hostname and (hostname.lower() == "redd.it" or hostname.lower().endswith(".redd.it")):
        return normalized
    return None


def configure_extractor() -> None:
    """Configure gallery-dl's Reddit extractor to use the configured OAuth client."""
    config.set(("extractor", "reddit"), key="client-id", value="yH0aTnJEt6qUgGn835B4vg")
    config.set(("extractor", "reddit"), key="user-agent", value="org.quantumbadger.redreader/1.25.1")
    config.set(("extractor", "reddit"), key="refresh-token", value=os.getenv("REDDIT_REFRESH_TOKEN", ""))


def configure_video_download(subreddit: str, post_id: str) -> None:
    """Configure gallery-dl to download a Reddit video and mux its audio track via ffmpeg."""
    configure_extractor()
    config.set(("extractor", "reddit"), key="previews", value=False)
    config.set(("extractor", "reddit"), key="embeds", value=False)
    config.set(("extractor", "reddit"), key="videos", value="dash")
    config.set(("extractor", "reddit"), key="directory", value=[])
    config.set(("extractor", "reddit"), key="filename", value=f"{post_id}.{{extension}}")
    config.set((), key="base-directory", value=str(reddit_media_path(subreddit, post_id).parent))
    config.set(("downloader", "ytdl"), key="format", value="bestvideo+bestaudio/best")
    config.set(("downloader", "ytdl"), key="raw-options", value={"merge_output_format": "mp4"})


def reddit_downloads_path(subreddit: str) -> Path:
    """Return the directory where a subreddit's post metadata is cached."""
    reddit_download_path: Path = data_dir() / "Reddit" / "Downloads" / subreddit
    reddit_download_path.mkdir(parents=True, exist_ok=True)
    return reddit_download_path


def reddit_media_path(subreddit: str, post_id: str) -> Path:
    """Return the path of a subreddit post's locally cached, muxed video file."""
    media_dir: Path = data_dir() / "Reddit" / "Media" / subreddit
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir / f"{post_id}.mp4"


def download_reddit_video(reddit_url: str, subreddit: str, post_id: str) -> Path:
    """Download a Reddit video with its audio track muxed together via gallery-dl and ffmpeg.

    Reddit serves video and audio as separate DASH streams, so linking the video-only
    fallback URL directly produces silent embeds. Downloading and muxing them locally
    lets us serve a single file with audio.

    Returns:
        The path to the locally cached, muxed video file.

    Raises:
        RuntimeError: If gallery-dl fails to download or produce the video.
    """
    output_path: Path = reddit_media_path(subreddit, post_id)
    if output_path.exists():
        return output_path

    logger.info("Downloading Reddit video with audio: {}", post_id)
    configure_video_download(subreddit, post_id)
    download_job: job.DownloadJob = job.DownloadJob(reddit_url)
    status: int = download_job.run()

    if status or not output_path.exists():
        msg: str = f"gallery-dl failed to download {reddit_url}"
        raise RuntimeError(msg)

    return output_path


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
    poster_source: dict[str, Any] = (preview_images[0].get("source") or {}) if preview_images else {}
    poster: str | None = _hosted_url(poster_source.get("url"))

    reddit_video: dict[str, Any] = (post.get("secure_media") or {}).get("reddit_video") or {}
    if video_url := _hosted_url(reddit_video.get("fallback_url")):
        return (
            [],
            {
                "url": video_url,
                "content_type": "video/mp4",
                "width": int(reddit_video.get("width") or 1280),
                "height": int(reddit_video.get("height") or 720),
                "is_gif": bool(reddit_video.get("is_gif")),
            },
            poster,
        )

    images: list[dict[str, Any]] = []
    metadata: dict[str, Any] = post.get("media_metadata") or {}
    for item in (post.get("gallery_data") or {}).get("items") or []:
        media: dict[str, Any] = metadata.get(item.get("media_id")) or {}
        source: dict[str, Any] = media.get("s") or {}
        if image_url := _hosted_url(source.get("u")):
            image: dict[str, Any] = {
                "url": image_url,
                "width": int(source.get("x") or 1280),
                "height": int(source.get("y") or 720),
            }
            if caption := item.get("caption"):
                image["description"] = str(caption)
            images.append(image)

    if not images and poster:
        images.append({
            "url": poster,
            "width": int(poster_source.get("width") or 1280),
            "height": int(poster_source.get("height") or 720),
        })

    return images, None, poster


def _serialize_discord_component_embed(payload: dict[str, Any]) -> str:
    """Serialize a component embed safely for an inline HTML script element."""
    serialized: str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Escaping '<' prevents user-provided text from terminating the script element.
    return (
        serialized
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build_discord_component_embed(
    title: str,
    description: str,
    canonical_url: str,
    images: list[dict[str, Any]],
    video: dict[str, Any] | None,
) -> str:
    """Build a Discord Component Embed and return its HTML-safe JSON string.

    The payload uses a text section, an optional link button, and an optional
    media gallery. It is kept within Discord's 3,000-byte payload limit.
    """
    media_items: list[dict[str, Any]] = []

    if video:
        video_url: Any = video.get("url")
        if isinstance(video_url, str) and video_url.startswith(("https://", "http://")) and len(video_url) <= 2048:
            media_items.append({"media": {"url": video_url}})
    else:
        for image in images[:10]:
            image_url: Any = image.get("url")
            if (
                not isinstance(image_url, str)
                or not image_url.startswith(("https://", "http://"))
                or len(image_url) > 2048
            ):
                continue

            item: dict[str, Any] = {"media": {"url": image_url}}
            if caption := image.get("description"):
                item["description"] = str(caption)[:200]
            media_items.append(item)

    heading: str = title[:250]
    body: str = description[:1200]

    def make_payload() -> dict[str, Any]:
        content: str = f"# {heading}" if heading else ""
        if body:
            content = f"{content}\n{body}" if content else body

        components: list[dict[str, Any]] = [
            {
                "type": 9,
                "components": [
                    {
                        "type": 10,
                        "content": content,
                    },
                ],
                "accessory": {
                    "type": 2,
                    "style": 5,
                    "url": canonical_url,
                    "label": "Open Reddit",
                },
            },
        ]

        if media_items:
            components.append({
                "type": 12,
                "items": media_items,
            })

        return {
            "component": {
                "type": 17,
                "accent_color": 0xFF4500,
                "components": components,
            },
        }

    payload: dict[str, Any] = make_payload()
    serialized: str = _serialize_discord_component_embed(payload)

    while len(serialized.encode("utf-8")) > 3000:
        if media_items:
            media_items.pop()
        elif body:
            body = body[: max(0, len(body) - 100)]
        elif heading:
            heading = heading[: max(0, len(heading) - 50)]
        else:
            # The remaining fixed payload is small enough for the 3,000-byte limit.
            break

        payload = make_payload()
        serialized = _serialize_discord_component_embed(payload)

    return serialized


@get("/reddit/media/{subreddit:str}/{post_id:str}/video.mp4", sync_to_thread=True)
def reddit_video_file(
    subreddit: Annotated[str, PathParameter()],
    post_id: Annotated[str, PathParameter()],
) -> File:
    """Serve a locally cached Reddit video with its audio track muxed in.

    Returns:
        The muxed video file.

    Raises:
        NotFoundException: If the video hasn't been downloaded yet.
    """
    video_path: Path = reddit_media_path(subreddit, post_id)
    if not video_path.exists():
        raise NotFoundException
    return File(path=video_path, media_type="video/mp4")


async def _resolve_video_url(request: Request, video: dict[str, Any], subreddit: str, post_id: str) -> str:
    """Download and mux a Reddit video's audio track, falling back to the hosted URL on failure.

    Returns:
        A URL to a locally hosted, muxed video, or the original Reddit-hosted URL on failure.
    """
    canonical_url: str = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}"
    try:
        await to_thread.run_sync(download_reddit_video, canonical_url, subreddit, post_id)
    except RuntimeError as exc:
        logger.warning("Failed to download Reddit video {}: {}", canonical_url, exc)
        return video["url"]
    return f"{str(request.base_url).rstrip('/')}/reddit/media/{subreddit}/{post_id}/video.mp4"


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
    """Serve a Discord Component Embed with Open Graph fallback for Reddit posts.

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

    if video and not video.pop("is_gif", False):
        video["url"] = await _resolve_video_url(request, video, subreddit, post_id)

    title: str = str(post.get("title") or "Reddit")
    author: str = str(post.get("author") or "reddit")
    post_subreddit: str = str(post.get("subreddit") or subreddit)
    upvotes: str = format_count(int(post.get("ups") or post.get("score") or 0))
    comments: str = format_count(int(post.get("num_comments") or 0))
    summary: str = f"u/{author} in r/{post_subreddit} | {upvotes} upvotes | {comments} comments"
    selftext: str = str(post.get("selftext") or "").strip()
    description: str = f"{summary}\n\n{selftext}" if selftext else summary

    component_embed_json: str = build_discord_component_embed(
        title=title,
        description=description,
        canonical_url=canonical_url,
        images=images,
        video=video,
    )

    og_image: dict[str, Any] | None = images[0] if images else ({"url": poster} if poster else None)

    response = Template(
        template_name="reddit.html",
        context={
            "images": images,
            "video": video,
            "poster": poster,
            "og_image": og_image,
            "og_type": "video.other" if video else "article",
            "title": title,
            "description": description,
            "canonical_url": canonical_url,
            "author": author,
            "subreddit": post_subreddit,
            "discord_component_embed_json": component_embed_json,
        },
    )
    response.headers["Cache-Control"] = "public, max-age=300"
    return response
