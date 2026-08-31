from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from functools import cache
from ipaddress import IPv4Address
from ipaddress import IPv6Address
from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from typing import Literal
from typing import TypedDict

import niquests
from litestar import Request
from litestar import get
from litestar.params import PathParameter
from litestar.response import Redirect
from litestar.response import Response
from litestar.response import Template
from loguru import logger

from e.discord import DiscordIPs
from e.discord import get_discord_ips
from e.settings import DATA_DIR

if TYPE_CHECKING:
    from pathlib import Path


def safe_int(val: str | int | None, default: int = 0) -> int:
    """Safely cast to int, preventing 500 errors when API returns null.

    Args:
        val: The value to cast to int.
        default: The default value to return if val is None or cannot be cast to int.

    Returns:
        The int value of val, or default if val is None or cannot be cast to int
    """
    if val is None:
        return default
    try:
        return int(val)
    except ValueError, TypeError:
        return default


def safe_float(val: str | int | None, default: float = 0.0) -> float:
    """Safely cast to float, preventing 500 errors when API returns null.

    Args:
        val: The value to cast to float.
        default: The default value to return if val is None or cannot be cast to float.

    Returns:
        The float value of val, or default if val is None or cannot be cast to float
    """
    if val is None:
        return default
    try:
        return float(val)
    except ValueError, TypeError:
        return default


def get_emoji_poop(tweet_id: str, *, html: bool = True) -> str:
    """Returns a string with emoji and engagement counts for a tweet."""
    tweet_data: dict[str, Any] = get_tweet(tweet_id=tweet_id)

    status: dict[str, Any] = tweet_data.get("status") or {}
    replies: int = safe_int(status.get("replies"))
    likes: int = safe_int(status.get("likes"))
    bookmarks: int = safe_int(status.get("bookmarks"))
    quotes: int = safe_int(status.get("quotes"))

    if not html:
        return f"💬 {replies} 🔁 {quotes} ❤️ {likes} 🔖 {bookmarks}"

    return f"<b>💬 {replies}&ensp;🔁 {quotes}&ensp;❤️ {likes}&ensp;🔖 {bookmarks}</b>"


def twitter_downloads_path(username: str) -> Path:
    """Returns path where Tweets are stored."""
    twitter_download_path: Path = DATA_DIR / "Twitter" / "Downloads" / f"{username}"
    twitter_download_path.mkdir(parents=True, exist_ok=True)
    return twitter_download_path


async def is_discord_client(client_ip: IPv4Address | IPv6Address) -> bool:
    """Check whether a client IP belongs to Discord (or localhost).

    Args:
        client_ip: The client IP address.

    Returns:
        ``True`` if the client is Discord or the loopback address.
    """
    if client_ip.is_loopback:
        return True

    ips: DiscordIPs = await get_discord_ips()
    return any(isinstance(client_ip, IPv4Address) and client_ip in prefix.ipv4_prefix for prefix in ips.prefixes)


@cache
def get_tweet(tweet_id: str) -> dict[str, Any]:
    """Returns tweet from fxtwitter API."""
    api_url: str = f"https://api.fxtwitter.com/2/status/{tweet_id}?about_account=1&lang=en"

    response: niquests.Response = niquests.get(
        api_url,
        headers={
            "user-agent": "https://github.com/TheLovinator1/e.lovinator.space",
        },
    )
    response.raise_for_status()

    json_data: dict[str, Any] = response.json()
    author: dict[str, Any] = json_data.get("author") or {}
    user_id: str = str(author.get("id") or "")

    twitter_download_path: Path = DATA_DIR / "Twitter" / "Downloads" / f"{user_id}"
    twitter_download_path.mkdir(parents=True, exist_ok=True)
    (twitter_download_path / f"{tweet_id}.json").write_text(str(json_data), encoding="utf-8")

    return json_data


class Photo(TypedDict):
    """TypedDict for a photo in a tweet."""

    type: str
    """The type of the photo, e.g., "photo"."""

    id: str
    """The unique identifier for the photo."""

    url: str
    """The URL of the photo."""

    width: int
    height: int
    """The height of the photo in pixels."""


class Video(TypedDict):
    """TypedDict for a video in a tweet."""

    id: str
    """The unique identifier for the video."""

    url: str
    """The URL of the video."""

    preview_url: str
    """The URL of the video's thumbnail."""

    duration: float
    """The duration of the video in seconds."""

    width: int
    """The width of the video in pixels."""

    height: int
    """The height of the video in pixels."""

    format: str
    """The format of the video, e.g., "video/mp4"."""

    content_type: str
    """The content type of the video, e.g., "video/mp4"."""


def build_mastodon_status(tweet_id: str, json_data: dict[str, Any]) -> dict[str, Any]:  # ruff: ignore[too-many-locals]
    """Generates the bulletproof Mastodon Status JSON, ensuring no missing fields crash the parser.

    Args:
        tweet_id: The ID of the tweet.
        json_data: The JSON data of the tweet.

    Returns:
        A dictionary representing the Mastodon Status JSON.
    """
    status: dict[str, Any] = json_data.get("status") or {}
    author: dict[str, Any] = json_data.get("author") or {}

    author_id: str = str(author.get("id") or "")
    author_name: str = str(author.get("name") or "Unknown")
    screen_name: str = str(author.get("screen_name") or "unknown")
    avatar: str = str(author.get("avatar_url") or "https://lovinator.space/KaoFace.png")

    created_timestamp = safe_int(status.get("created_timestamp"))
    created_at: str = datetime.fromtimestamp(created_timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    text: str = str(status.get("text") or "")
    content: str = text.replace("\n", "<br>\u200a\u200a")
    stats_html: str = get_emoji_poop(tweet_id=tweet_id, html=True)
    content = f"{content}<br><br>{stats_html}"

    url: str = f"https://e.lovinator.space/{screen_name}/status/{tweet_id}"

    payload: dict[str, Any] = {
        "id": tweet_id,
        "url": url,
        "uri": url,
        "created_at": created_at,
        "edited_at": None,
        "reblog": None,
        "in_reply_to_id": None,
        "in_reply_to_account_id": None,
        "language": "en",
        "content": content,
        "spoiler_text": "",
        "visibility": "public",
        "application": {"name": "Twitter", "website": None},
        "media_attachments": [],
        "account": {
            "id": author_id,
            "display_name": author_name,
            "username": screen_name,
            "acct": screen_name,
            "url": url,
            "uri": url,
            "created_at": created_at,
            "locked": False,
            "bot": False,
            "discoverable": True,
            "indexable": False,
            "group": False,
            "avatar": avatar,
            "avatar_static": avatar,
            "header": "",
            "header_static": "",
            "followers_count": safe_int(author.get("followers")),
            "following_count": safe_int(author.get("following")),
            "statuses_count": safe_int(author.get("statuses")),
            "hide_collections": False,
            "noindex": False,
            "emojis": [],
            "roles": [],
            "fields": [],
        },
        "mentions": [],
        "tags": [],
        "emojis": [],
        "card": None,
        "poll": None,
    }

    media: dict[str, Any] = status.get("media") or {}
    if photos := media.get("photos"):
        for photo in photos:
            photo_url = str(photo.get("url") or "")
            w = safe_int(photo.get("width"))
            h = safe_int(photo.get("height"))
            aspect = (w / h) if h > 0 else 0.0

            payload["media_attachments"].append({
                "id": str(photo.get("id") or "0"),
                "type": "image",
                "url": photo_url,
                "preview_url": None,
                "remote_url": None,
                "preview_remote_url": None,
                "text_url": None,
                "description": f"Photo by {screen_name} on Twitter",
                "meta": {
                    "original": {
                        "width": w,
                        "height": h,
                        "size": f"{w}x{h}",
                        "aspect": aspect,
                    }
                },
            })

    if json_videos := media.get("videos"):
        videos: list[dict] = json_videos
        for video in videos:
            orig_w = safe_int(video.get("width"), 1280)
            orig_h = safe_int(video.get("height"), 720)

            mult = 1.0
            if orig_w > 1920 or orig_h > 1920:  # ruff: ignore[magic-value-comparison]
                mult = 0.5
            if orig_w < 400 and orig_h < 400 and orig_w > 0:  # ruff: ignore[magic-value-comparison]
                mult = 2.0

            final_w = int(orig_w * mult)
            final_h = int(orig_h * mult)
            video_url = str(video.get("url") or "")
            duration = safe_float(video.get("duration"))
            aspect = (final_w / final_h) if final_h > 0 else 0.0

            payload["media_attachments"].append({
                "id": str(video.get("id") or "0"),
                "type": "video",
                "url": video_url,
                "preview_url": str(video.get("thumbnail_url") or ""),
                "remote_url": None,
                "preview_remote_url": None,
                "text_url": None,
                "description": f"Video by {screen_name} on Twitter",
                "meta": {
                    "original": {
                        "width": final_w,
                        "height": final_h,
                        "size": f"{final_w}x{final_h}",
                        "aspect": aspect,
                        "duration": duration,
                    }
                },
            })

    return payload


@get("/{username:str}/status/{tweet_id:str}")
async def twitter(  # ruff: ignore[too-many-locals, unused-async]
    request: Request,
    username: Annotated[str, PathParameter()],
    tweet_id: Annotated[str, PathParameter()],
) -> Response | Redirect:
    """Serve an Open Graph embed for a tweet to Discord clients.

    Returns:
        A redirect to the tweet URL for non-Discord clients, or a rendered
        HTML page with Open Graph metadata for Discord clients.
    """
    try:
        json_data: dict[str, Any] = get_tweet(tweet_id=tweet_id)
    except niquests.HTTPError as e:
        logger.error("Failed to fetch tweet: %s", tweet_id, exc_info=e)
        return Redirect(f"https://x.com/{username}/status/{tweet_id}", status_code=302)

    status: dict[str, Any] = json_data.get("status") or {}
    author: dict[str, Any] = json_data.get("author") or {}
    media: dict[str, Any] = status.get("media") or {}

    # Lock everything exactly to API screen_name so JSON & HTML match perfectly
    screen_name: str = str(author.get("screen_name") or username)
    tweet_url: str = f"https://x.com/{screen_name}/status/{tweet_id}"
    local_url: str = f"https://e.lovinator.space/{screen_name}/status/{tweet_id}"

    logger.info("Serving tweet embed: %s", tweet_id)

    text: str = str(status.get("text") or "").strip()
    title: str = text[:200]
    emoji_poop: str = get_emoji_poop(tweet_id=tweet_id, html=False)

    photos: list[Photo] = [
        {
            "type": "photo",
            "id": str(item.get("id") or "0"),
            "url": str(item.get("url") or ""),
            "width": safe_int(item.get("width"), 1280),
            "height": safe_int(item.get("height"), 720),
        }
        for item in (media.get("photos") or [])
        if item.get("url")
    ]

    video: Video | None = None
    if videos := media.get("videos"):
        item: dict[str, Any] = videos[0]
        if item.get("url"):
            orig_w = safe_int(item.get("width"), 1280)
            orig_h = safe_int(item.get("height"), 720)
            mult = 1.0
            if orig_w > 1920 or orig_h > 1920:  # ruff: ignore[magic-value-comparison]
                mult = 0.5
            if orig_w < 400 and orig_h < 400 and orig_w > 0:  # ruff: ignore[magic-value-comparison]
                mult = 2.0

            video = {
                "id": str(item.get("id") or "0"),
                "url": str(item.get("url") or ""),
                "preview_url": str(item.get("thumbnail_url") or ""),
                "duration": safe_float(item.get("duration")),
                "width": int(orig_w * mult),
                "height": int(orig_h * mult),
                "format": str(item.get("format") or ""),
                "content_type": str(item.get("content_type") or "video/mp4"),
            }

    poster: str | None = video["preview_url"] if video else None
    og_type: Literal["video.other", "article"] = "video.other" if video else "article"

    stats: dict[str, int] = {
        "followers": safe_int(author.get("followers")),
        "following": safe_int(author.get("following")),
        "likes": safe_int(author.get("likes")),
        "media_count": safe_int(author.get("media_count")),
        "statuses": safe_int(author.get("statuses")),
    }

    name: str = str(author.get("name") or "Unknown")

    response = Template(
        template_name="tweet.html",
        context={
            "stats": stats,
            "name": name,
            "images": photos,
            "video": video,
            "poster": poster,
            "width": int(video["width"]) if video else 1280,
            "height": int(video["height"]) if video else 720,
            "og_type": og_type,
            "twitter_handle": f"@{screen_name}",
            "tweet_url": tweet_url,
            "username": screen_name,
            "tweet_id": tweet_id,
            "title": title,
            "description": emoji_poop,
            "url": local_url,
            "site": f"@{screen_name}",
            "creator": f"@{screen_name}",
            "activity_url": f"https://e.lovinator.space/users/{screen_name}/statuses/{tweet_id}",
            "oembed_url": f"https://e.lovinator.space/_oembed/{screen_name}/{tweet_id}",
        },
    )

    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@get(path="/api/v1/statuses/{tweet_id:str}")
async def tweet_status_api(  # ruff: ignore[unused-async]
    request: Request,
    tweet_id: Annotated[str, PathParameter()],
) -> Response:
    """The Mastodon Status Endpoint.

    Discord calls GET /api/v1/statuses/:id after detecting the activity+json link.
    Return JSON matching the Mastodon Status entity format.
    Both this endpoint and /users/:handle/statuses/:id must return Content-Type: application/json.

    Args:
        request: The incoming request.
        tweet_id: The status ID of the tweet.

    Returns:
        The Status document as JSON.
    """
    try:
        json_data: dict[str, Any] = get_tweet(tweet_id=tweet_id)
    except niquests.HTTPError:
        return Response(content={"error": "Not found"}, status_code=404, media_type="application/json")

    payload = build_mastodon_status(tweet_id, json_data)
    return Response(content=json.dumps(payload), media_type="application/json")


@get("/users/{username:str}/statuses/{tweet_id:str}")
async def users_statuses(  # ruff: ignore[unused-async]
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
    try:
        json_data: dict[str, Any] = get_tweet(tweet_id=tweet_id)
    except niquests.HTTPError:
        return Response(content={"error": "Not found"}, status_code=404, media_type="application/json")

    payload = build_mastodon_status(tweet_id, json_data)
    return Response(content=json.dumps(payload), media_type="application/json")


@get("/_oembed/{username:str}/{tweet_id:str}")
async def tweet_oembed(  # ruff: ignore[unused-async]
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
    try:
        get_tweet(tweet_id=tweet_id)  # ensure it exists
    except niquests.HTTPError:
        return Response(content={"error": "Not found"}, status_code=404, media_type="application/json")

    # Renders as old text at top of embed.
    # Use for engagement stats, reply indicators, or any primary label.
    # This OVERRIDES the Mastodon account.display_name.
    # TODO(TheLovinator): Add reply indicators: "↪ Replying to @another_user"  # ruff: ignore[missing-todo-link]
    # TODO(TheLovinator): Add Thread indicator: "↪ Thread by @user"  # ruff: ignore[missing-todo-link]
    author_name: str = get_emoji_poop(tweet_id=tweet_id, html=False)

    # Link target for the author line.
    # Usually the original post URL.
    author_url: str = f"https://x.com/{username}"

    # Footer text for the embed.
    # Your branding, e.g., "convert.cat". Can include context: "GIF · convert.cat"
    provider_name: str = "oEmbed e.lovinator.space"

    # Footer link target.
    # Your site URL or the original post URL.
    provider_url: str = "https://e.lovinator.space"

    # type is required and must be "rich" for Discord to render the embed.
    # version is required and must be "1.0" for Discord to render the embed.
    payload: dict[str, str] = {
        "type": "rich",
        "version": "1.0",
        "author_name": author_name,
        "author_url": author_url,
        "provider_name": provider_name,
        "provider_url": provider_url,
        "title": "Embed",
    }

    return Response(
        content=json.dumps(payload),
        media_type="application/json",
    )
