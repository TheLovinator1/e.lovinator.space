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

    (twitter_downloads_path(user_id) / f"{tweet_id}.json").write_text(str(json_data), encoding="utf-8")
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

    thumbnail_url: str
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
async def twitter(
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
    description: str = text[:1000]

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
                "thumbnail_url": str(item.get("thumbnail_url") or ""),
                "duration": float(item.get("duration") or 0),
                "width": int(item.get("width") or 1280),
                "height": int(item.get("height") or 720),
                "format": str(item.get("format") or ""),
                "content_type": str(item.get("content_type") or "video/mp4"),
            }

    poster: str | None = video["thumbnail_url"] if video else None
    og_type: Literal["video.other", "article"] = "video.other" if video else "article"

    stats: dict[str, int] = {
        "followers": int(author.get("followers") or 0),
        "following": int(author.get("following") or 0),
        "likes": int(author.get("likes") or 0),
        "media_count": int(author.get("media_count") or 0),
        "statuses": int(author.get("statuses") or 0),
    }

    response = Template(
        template_name="tweet.html",
        context={
            "stats": stats,
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
            "description": description,
            "url": tweet_url,
            "site": f"@{username}",
            "creator": f"@{username}",
            "activity_url": f"/api/v1/statuses/{tweet_id}",
            "oembed_url": f"/_oembed/{username}/{tweet_id}",
        },
    )

    response.headers["Cache-Control"] = "public, max-age=300"

    return response


@get(path="/api/v1/statuses/{tweet_id:str}")
async def tweet_status_api(
    request: Request,
    tweet_id: Annotated[str, PathParameter()],
) -> Response:
    """Serve a Mastodon API ``Status`` document for a tweet.

    Discord's crawler probes this REST endpoint in addition to the alternate
    links on the embed page; the document matches the activity one.

    Args:
        request: The incoming request.
        tweet_id: The status ID of the tweet.

    Returns:
        The Status document as JSON.
    """
    json_data: dict[str, Any] = get_tweet(tweet_id=tweet_id)
    author: dict[str, Any] = json_data.get("author", {})
    screen_name: str = author.get("screen_name", "screen_name")
    username: str = author.get("name", "screen_name")
    avatar: str = author.get("avatar_url", "https://lovinator.space/KaoFace.png")

    status: dict[str, Any] = json_data.get("status", {})
    created_timestamp: int = json_data.get("created_timestamp", 0)

    created_at: str = datetime.fromtimestamp(created_timestamp, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    content: str = status.get("text", "")
    replies: int = status.get("replies", 0)
    likes: int = status.get("likes", 0)
    bookmarks: int = status.get("bookmarks", 0)
    quotes: int = status.get("quotes", 0)

    emoji_poop: str = (
        f"{content}<br>\ufe00\ufe00<br><br><b>💬 {replies}&ensp;🔁 {quotes}&ensp;❤️ {likes}&ensp;🔖 {bookmarks}</b>"
    )
    avatar = ""
    url: str = f"https://twitter.com/{username}/status/{tweet_id}"

    status: dict[str, Any] = {
        "id": "1234567890987654321",
        "url": f"{url}",
        "created_at": created_at,
        "content": f"{emoji_poop}",
        "account": {
            "id": f"{screen_name}",
            "username": f"{screen_name}",
            "acct": f"{screen_name}",
            "display_name": f"{username}",
            "url": f"{url}",
            "created_at": created_at,
            "locked": False,
            "bot": False,
            "discoverable": True,
            "indexable": True,
            "group": False,
            "followers_count": 0,
            "following_count": 0,
            "statuses_count": 0,
            "hide_collections": False,
            "noindex": False,
            "emojis": [],
            "roles": [],
            "fields": [],
            "avatar": f"{avatar}",
            "avatar_static": f"{avatar}",
        },
        "media_attachments": [
            {
                "id": "0",
                "type": "image",
                "url": "https://pbs.twimg.com/media/image123.jpg",
                "preview_url": "https://pbs.twimg.com/media/image123.jpg",
                "remote_url": None,
                "preview_remote_url": None,
                "text_url": None,
                "description": None,
            }
        ],
    }

    return Response(
        content=json.dumps(status),
        media_type="application/json",
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
    author_name: str = username
    author_url: str = f"https://x.com/{username}"

    payload: dict[str, str] = {
        "type": "rich",
        "version": "1.0",
        "author_name": author_name,
        "author_url": author_url,
        "provider_name": "Mastodon-style e.lovinator.space",
        "provider_url": "https://e.lovinator.space",
        "title": "Embed",
    }

    return Response(
        content=json.dumps(payload),
        media_type="application/json",
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
    author_name: str = username
    author_url: str = f"https://x.com/{username}"
    provider_url: str = "https://e.lovinator.space"

    payload: dict[str, str] = {
        "type": "rich",
        "version": "1.0",
        "author_name": author_name,
        "author_url": author_url,
        "provider_name": "oEmbed e.lovinator.space",
        "provider_url": provider_url,
        "title": "Embed",
    }

    return Response(
        content=json.dumps(payload),
        media_type="application/json",
    )
