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


def get_emoji_poop(tweet_id: str, *, html: bool = True) -> str:
    """Returns a string with emoji and engagement counts for a tweet.

    Args:
        tweet_id: The ID of the tweet.
        html: Whether to return the string as HTML or plain text.

    Returns:
        A string with emoji and engagement counts for the tweet.
    """
    tweet_data: dict[str, Any] = get_tweet(tweet_id=tweet_id)

    status: dict[str, Any] = tweet_data.get("status", {})
    replies: int = status.get("replies", 0)
    likes: int = status.get("likes", 0)
    bookmarks: int = status.get("bookmarks", 0)
    quotes: int = status.get("quotes", 0)

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
    # https://api.fxtwitter.com/2/status/2092632316522709394?about_account=1&lang=en
    api_url: str = f"https://api.fxtwitter.com/2/status/{tweet_id}?about_account=1&lang=en"

    response: niquests.Response = niquests.get(
        api_url,
        headers={
            "user-agent": "https://github.com/TheLovinator1/e.lovinator.space",
        },
    )
    response.raise_for_status()

    json_data: dict[str, Any] = response.json()
    author: dict[str, Any] = json_data.get("author", {})
    user_id: str = author.get("id", "")

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
    """The width of the photo in pixels."""

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
    tweet_url: str = f"https://x.com/{username}/status/{tweet_id}"
    logger.info("Serving tweet embed: %s", tweet_id)

    try:
        json_data: dict[str, Any] = get_tweet(tweet_id=tweet_id)
    except niquests.HTTPError as e:
        logger.error("Failed to fetch tweet: %s", tweet_id, exc_info=e)
        return Redirect(tweet_url, status_code=302)

    status: dict[str, Any] = json_data.get("status") or {}
    author: dict[str, Any] = json_data.get("author") or {}
    media: dict[str, Any] = status.get("media") or {}

    text: str = str(status.get("text") or "").strip()
    title: str = text[:200]

    emoji_poop: str = get_emoji_poop(tweet_id=tweet_id, html=False)
    emoji_poop: str = f"a{emoji_poop}"

    photos: list[Photo] = [
        {
            "type": "photo",
            "id": str(item.get("id") or ""),
            "url": str(item.get("url") or ""),
            "width": int(item.get("width") or 1280),
            "height": int(item.get("height") or 720),
        }
        for item in (media.get("photos") or [])
        if item.get("url")
    ]

    video: Video | None = None
    if videos := media.get("videos"):
        item: dict[str, Any] = videos[0]

        if item.get("url"):
            video = {
                "id": str(item.get("id") or ""),
                "url": str(item.get("url") or ""),
                "preview_url": str(item.get("thumbnail_url") or ""),
                "duration": float(item.get("duration") or 0),
                "width": int(item.get("width") or 1280),
                "height": int(item.get("height") or 720),
                "format": str(item.get("format") or ""),
                "content_type": str(item.get("content_type") or "video/mp4"),
            }

    poster: str | None = video["preview_url"] if video else None
    og_type: Literal["video.other", "article"] = "video.other" if video else "article"

    stats: dict[str, int] = {
        "followers": int(author.get("followers") or 0),
        "following": int(author.get("following") or 0),
        "likes": int(author.get("likes") or 0),
        "media_count": int(author.get("media_count") or 0),
        "statuses": int(author.get("statuses") or 0),
    }

    name: str = author.get("name", "name")

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
            "twitter_handle": f"@{username}",
            "tweet_url": tweet_url,
            "username": username,
            "tweet_id": tweet_id,
            "title": title,
            "description": emoji_poop,
            "url": tweet_url,
            "site": f"@{username}",
            "creator": f"@{username}",
            "activity_url": f"https://e.lovinator.space/users/{username}/statuses/{tweet_id}",
            "oembed_url": f"https://e.lovinator.space/_oembed/{username}/{tweet_id}",
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
    json_data: dict[str, Any] = get_tweet(tweet_id=tweet_id)
    status: dict[str, Any] = json_data.get("status", {})
    author: dict[str, Any] = json_data.get("author", {})

    author_id: str = author.get("id", "")
    screen_name: str = author.get("screen_name", "screen_name")
    username: str = author.get("name", "screen_name")
    avatar: str = author.get("avatar_url", "https://lovinator.space/KaoFace.png")

    created_timestamp: int = status.get("created_timestamp", 0)
    created_at: str = datetime.fromtimestamp(created_timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    text: str = status.get("text", "")
    content: str = text.replace("\n", "<br>\u200a\u200a")

    stats_html: str = get_emoji_poop(tweet_id=tweet_id, html=True)
    content = f"{content}<br><br>{stats_html}"

    url: str = f"https://twitter.com/{username}/status/{tweet_id}"

    payload: dict[str, Any] = {
        "id": tweet_id,
        "url": url,
        "uri": url,
        "created_at": created_at,
        "content": content,
        "visibility": "public",
        "account": {
            "id": author_id,
            "display_name": screen_name,
            "username": username,
            "acct": username,
            "url": url,
            "avatar": avatar,
            "avatar_static": avatar,
        },
        "media_attachments": [],
        "mentions": [],
        "tags": [],
        "emojis": [],
    }

    # Append media attachments if present
    media: dict[str, Any] = status.get("media", {})
    if photos := media.get("photos"):
        for photo in photos:
            url = photo.get("url", "")

            payload["media_attachments"].append({
                "id": photo.get("id", ""),
                "type": "image",
                "url": url,
                "preview_url": None,
                "remote_url": None,
                "preview_remote_url": None,
                "text_url": None,
                "description": "Alt text for accessibility",
                "meta": {
                    "original": {
                        "width": photo.get("width", 0),
                        "height": photo.get("height", 0),
                        "size": f"{photo.get('width', 0)}x{photo.get('height', 0)}",
                        "aspect": photo.get("width", 0) / photo.get("height", 1) if photo.get("height", 0) else 0,
                    }
                },
            })

    if json_videos := media.get("videos"):
        # TODO(TheLovinator): Handle multiple videos in a tweet, if applicable. Currently only the first video is processed.  # ruff: ignore[missing-todo-link]

        # TODO(TheLovinator): Handle video scaling for Discord embeds.  # ruff: ignore[missing-todo-link]
        # Discord refuses to render videos with dimensions >1920px and renders very small videos as tiny embeds. Apply this scaling:
        # let sizeMultiplier = 1
        # if (width > 1920 || height > 1920) {
        # sizeMultiplier = 0.5   // Scale down 50%
        # }
        # if (width < 400 && height < 400) {
        # sizeMultiplier = 2     // Scale up 200%
        # }
        # Note: The downscale check uses OR (either dimension >1920), but the upscale check uses AND (both dimensions <400).
        # Also look at HTML:
        # <meta property="og:video:width" content="960"/>   <!-- was 1920, scaled 50% -->
        # <meta property="og:video:height" content="540"/>   <!-- was 1080, scaled 50% -->
        # <meta property="twitter:player:width" content="960"/>
        # <meta property="twitter:player:height" content="540"/>

        videos: list[Video] = json_videos

        for video in videos:
            url = video.get("url", "")

            payload["media_attachments"].append({
                "id": video.get("id", ""),
                "type": "video",
                "url": url,
                "preview_url": video.get("thumbnail_url", ""),
                "remote_url": None,
                "preview_remote_url": None,
                "text_url": None,
                "description": "Alt text for accessibility",
                "meta": {
                    "original": {
                        "width": video.get("width", 0),
                        "height": video.get("height", 0),
                        "size": f"{video.get('width', 0)}x{video.get('height', 0)}",
                        "aspect": video.get("width", 0) / video.get("height", 1) if video.get("height", 0) else 0,
                        "duration": video.get("duration", 0),
                    }
                },
            })

    return Response(
        content=json.dumps(payload),
        media_type="application/json",
    )


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
    json_data: dict[str, Any] = get_tweet(tweet_id=tweet_id)
    status: dict[str, Any] = json_data.get("status", {})
    author: dict[str, Any] = json_data.get("author", {})

    author_id: str = author.get("id", "")
    screen_name: str = author.get("screen_name", "screen_name")
    avatar: str = author.get("avatar_url", "https://lovinator.space/KaoFace.png")

    created_timestamp: int = status.get("created_timestamp", 0)
    created_at: str = datetime.fromtimestamp(created_timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    text: str = status.get("text", "")
    content: str = text.replace("\n", "<br>\u200a\u200a")

    stats_html: str = get_emoji_poop(tweet_id=tweet_id, html=True)
    content = f"{content}<br><br>{stats_html}"

    url: str = f"https://twitter.com/{username}/status/{tweet_id}"

    payload: dict[str, Any] = {
        "id": tweet_id,
        "url": url,
        "uri": url,
        "created_at": created_at,
        "content": content,
        "account": {
            "id": author_id,
            "display_name": screen_name,
            "username": username,
            "acct": username,
            "url": url,
            "avatar": avatar,
            "avatar_static": avatar,
        },
        "media_attachments": [],
        "mentions": [],
        "tags": [],
        "emojis": [],
    }

    # Append media attachments if present
    media: dict[str, Any] = status.get("media", {})
    if photos := media.get("photos"):
        for photo in photos:
            url = photo.get("url", "")

            payload["media_attachments"].append({
                "id": photo.get("id", ""),
                "type": "image",
                "url": url,
                "preview_url": None,
                "remote_url": None,
                "preview_remote_url": None,
                "text_url": None,
                "description": f"Photo by {username} on Twitter",
                "meta": {
                    "original": {
                        "width": photo.get("width", 0),
                        "height": photo.get("height", 0),
                        "size": f"{photo.get('width', 0)}x{photo.get('height', 0)}",
                        "aspect": photo.get("width", 0) / photo.get("height", 1) if photo.get("height", 0) else 0,
                    }
                },
            })

    if json_videos := media.get("videos"):
        # TODO(TheLovinator): Handle multiple videos in a tweet, if applicable. Currently only the first video is processed.  # ruff: ignore[missing-todo-link]

        # TODO(TheLovinator): Handle video scaling for Discord embeds.  # ruff: ignore[missing-todo-link]
        # Discord refuses to render videos with dimensions >1920px and renders very small videos as tiny embeds. Apply this scaling:
        # let sizeMultiplier = 1
        # if (width > 1920 || height > 1920) {
        # sizeMultiplier = 0.5   // Scale down 50%
        # }
        # if (width < 400 && height < 400) {
        # sizeMultiplier = 2     // Scale up 200%
        # }
        # Note: The downscale check uses OR (either dimension >1920), but the upscale check uses AND (both dimensions <400).
        # Also look at HTML:
        # <meta property="og:video:width" content="960"/>   <!-- was 1920, scaled 50% -->
        # <meta property="og:video:height" content="540"/>   <!-- was 1080, scaled 50% -->
        # <meta property="twitter:player:width" content="960"/>
        # <meta property="twitter:player:height" content="540"/>

        videos: list[Video] = json_videos

        for video in videos:
            url = video.get("url", "")

            payload["media_attachments"].append({
                "id": video.get("id", ""),
                "type": "video",
                "url": url,
                "preview_url": video.get("thumbnail_url", ""),
                "remote_url": None,
                "preview_remote_url": None,
                "text_url": None,
                "description": "Alt text for accessibility",
                "meta": {
                    "original": {
                        "width": video.get("width", 0),
                        "height": video.get("height", 0),
                        "size": f"{video.get('width', 0)}x{video.get('height', 0)}",
                        "aspect": video.get("width", 0) / video.get("height", 1) if video.get("height", 0) else 0,
                        "duration": video.get("duration", 0),
                    }
                },
            })

    return Response(
        content=json.dumps(payload),
        media_type="application/json",
    )


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
    # Renders as old text at top of embed.
    # Use for engagement stats, reply indicators, or any primary label.
    # This OVERRIDES the Mastodon account.display_name.
    # TODO(TheLovinator): Add reply indicators: "↪ Replying to @another_user"  # ruff: ignore[missing-todo-link]
    # TODO(TheLovinator): Add Thread indicator: "↪ Thread by @user"  # ruff: ignore[missing-todo-link]
    author_name: str = get_emoji_poop(tweet_id=tweet_id, html=False)
    author_name: str = f"c{author_name}"

    # Link target for the author line.
    # Usually the original post URL.
    author_url: str = f"https://x.com/{username}"

    # Footer text for the embed.
    # Your branding, e.g., "convert.cat". Can include context: "GIF · convert.cat"
    provider_name: str = "oEmbed e.lovinator.space"

    # Footer link target.
    # Your site URL or the original post URL.
    provider_url: str = "https://e.lovinator.space"

    # Not visibly rendered.
    # Required field, set to "Embed".
    title: str = "Embed"

    # type is required and must be "rich" for Discord to render the embed.
    # version is required and must be "1.0" for Discord to render the embed.
    payload: dict[str, str] = {
        "type": "rich",
        "version": "1.0",
        "author_name": author_name,
        "author_url": author_url,
        "provider_name": provider_name,
        "provider_url": provider_url,
        "title": title,
    }

    return Response(
        content=json.dumps(payload),
        media_type="application/json",
    )
