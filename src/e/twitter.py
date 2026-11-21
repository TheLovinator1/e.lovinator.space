from __future__ import annotations

import html
import json
import re
from datetime import UTC
from datetime import datetime
from ipaddress import IPv4Address
from ipaddress import IPv6Address
from ipaddress import ip_address
from typing import TYPE_CHECKING
from typing import Annotated
from typing import Any
from typing import Literal
from typing import TypedDict
from urllib.parse import quote
from urllib.parse import urlparse

import niquests
from dotenv import load_dotenv
from litestar import Request
from litestar import get
from litestar.exceptions import NotFoundException
from litestar.params import PathParameter
from litestar.response import File
from litestar.response import Redirect
from litestar.response import Response
from litestar.response import Template
from loguru import logger
from platformdirs import PlatformDirs

from e.discord import DiscordIPs
from e.discord import get_discord_ips

if TYPE_CHECKING:
    from pathlib import Path

load_dotenv()


def data_dir() -> Path:
    """Return the application data directory."""
    return PlatformDirs(appauthor="TheLovinator", appname="e", ensure_exists=True, roaming=True).user_data_path


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


def format_count(count: int) -> str:
    """Returns K, M, B abbreviations for large numbers."""
    if count >= 1_000_000_000:  # ruff: ignore[magic-value-comparison]
        return f"{count / 1_000_000_000:.1f}B"
    if count >= 1_000_000:  # ruff: ignore[magic-value-comparison]
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:  # ruff: ignore[magic-value-comparison]
        return f"{count / 1_000:.1f}K"

    return str(count)


def get_emoji_poop(status: dict[str, Any], *, html: bool = True) -> str:
    """Returns a string with emoji and engagement counts for a tweet."""
    emoji_poop: str = ""

    bookmarks: int = safe_int(status.get("bookmarks"))
    likes: int = safe_int(status.get("likes"))
    replies: int = safe_int(status.get("replies"))
    reposts: int = safe_int(status.get("reposts"))
    views: int = safe_int(status.get("views"))

    if not html:
        if views > 0:
            emoji_poop += f"👁️ {format_count(views)} "
        if replies > 0:
            emoji_poop += f"💬 {format_count(replies)} "
        if reposts > 0:
            emoji_poop += f"🔁 {format_count(reposts)} "
        if likes > 0:
            emoji_poop += f"❤️ {format_count(likes)} "
        if bookmarks > 0:
            emoji_poop += f"🔖 {format_count(bookmarks)} "
        return emoji_poop

    if views > 0:
        emoji_poop += f"👁️ {format_count(views)}&ensp;"
    if replies > 0:
        emoji_poop += f"💬 {format_count(replies)}&ensp;"
    if reposts > 0:
        emoji_poop += f"🔁 {format_count(reposts)}&ensp;"
    if likes > 0:
        emoji_poop += f"❤️ {format_count(likes)}&ensp;"
    if bookmarks > 0:
        emoji_poop += f"🔖 {format_count(bookmarks)}&ensp;"
    return f"<b>{emoji_poop}</b>"


def twitter_downloads_path(username: str) -> Path:
    """Returns path where Tweets are stored."""
    twitter_download_path: Path = data_dir() / "Twitter" / "Downloads" / f"{username}"
    twitter_download_path.mkdir(parents=True, exist_ok=True)
    return twitter_download_path


def get_client_ip(request: Request) -> IPv4Address | IPv6Address | None:
    """Extract client IP address from the request.

    Checks X-Forwarded-For, X-Real-IP, CF-Connecting-IP, and request.client.host.

    Args:
        request: The incoming request.

    Returns:
        An IPv4Address or IPv6Address if parsed successfully, otherwise None.
    """
    raw_ip: str | None = None

    if xff := request.headers.get("x-forwarded-for"):
        raw_ip = xff.split(",")[0].strip()
    elif x_real_ip := request.headers.get("x-real-ip"):
        raw_ip = x_real_ip.strip()
    elif cf_ip := request.headers.get("cf-connecting-ip"):
        raw_ip = cf_ip.strip()
    elif request.client and request.client.host:
        raw_ip = request.client.host.strip()

    if not raw_ip:
        return None

    try:
        return ip_address(raw_ip)
    except ValueError:
        return None


async def is_discord_client(client_ip: IPv4Address | IPv6Address) -> bool:
    """Check whether a client IP belongs to Discord (or localhost).

    Args:
        client_ip: The client IP address.

    Returns:
        ``True`` if the client is Discord or the loopback address.
    """
    if client_ip.is_loopback:
        return True

    try:
        ips: DiscordIPs = await get_discord_ips()
    except Exception as e:  # ruff: ignore[blind-except]
        logger.warning(f"Failed to fetch Discord IPs: {e}")
        return False

    # Discord's IP range feed only ever publishes IPv4 prefixes, so IPv6 clients never match here.
    return any(isinstance(client_ip, IPv4Address) and client_ip in prefix.ipv4_prefix for prefix in ips.prefixes)


def convert_urls_to_links(text: str, facets: list[dict[str, Any]]) -> str:
    """Replace URL and hashtag facets in tweet text with HTML anchors.

    Matches by substring rather than facet indices: indices are only valid against
    ``raw_text.text``, but ``text`` here may instead be a translated replacement whose
    length/offsets don't line up with those indices (see ``get_tweet``).

    The whole text is HTML-escaped first so tweet content can never inject markup; anchors
    are then spliced in by matching against the escaped form of each facet's URL.

    Args:
        text: Tweet text where URL facets have already been resolved to their real destinations.
        facets: The facets from "raw_text.facets".

    Returns:
        The escaped text with resolved URLs wrapped in <a> HTML tags.
    """
    anchors: dict[str, str] = {}
    for facet in facets:
        facet_type: str = str(facet.get("type") or "")

        if facet_type == "hashtag":
            hashtag: str = str(facet.get("original") or "")
            if not hashtag:
                continue

            escaped_hashtag: str = html.escape(f"#{hashtag}", quote=True)
            if escaped_hashtag in anchors:
                continue

            hashtag_url: str = f"https://x.com/hashtag/{quote(hashtag, safe='')}"
            anchors[escaped_hashtag] = f'<a href="{hashtag_url}">{escaped_hashtag}</a>'
            continue

        if facet_type != "url":
            continue

        replacement: str = str(facet.get("replacement") or "")
        if not replacement:
            continue

        # Only linkify http(s) URLs; reject javascript:/data:/etc. schemes that could execute on click.
        if urlparse(replacement).scheme.lower() not in {"http", "https"}:
            continue

        escaped_replacement: str = html.escape(replacement, quote=True)
        if escaped_replacement in anchors:
            continue

        label: str = html.escape(re.sub(r"^https?://", "", replacement), quote=True)
        anchors[escaped_replacement] = f'<a href="{escaped_replacement}">{label}</a>'

    escaped_text: str = html.escape(text, quote=True)

    if not anchors:
        return escaped_text

    # Match longest URLs first so a shorter URL that's a substring of a longer one
    # (e.g. https://example.com/path vs https://example.com/path/extra) can't clobber it,
    # and do it in a single pass so an inserted anchor's href is never re-scanned.
    pattern = re.compile("|".join(re.escape(url) for url in sorted(anchors, key=len, reverse=True)))
    return pattern.sub(lambda match: anchors[match.group(0)], escaped_text)


def tweet_cache_path(tweet_id: str) -> Path:
    """Return the path where a tweet's raw fxtwitter JSON is stored.

    Args:
        tweet_id: The status ID of the tweet.

    Returns:
        The path of the cache file.
    """
    return data_dir() / "Twitter" / "Downloads" / f"{tweet_id}.json"


def find_tweet_cache(tweet_id: str) -> Path | None:
    """Find cached JSON for a tweet, including caches from older user-ID folders.

    Args:
        tweet_id: The status ID of the tweet.

    Returns:
        The path of the cached JSON, or None when there is no cache file.
    """
    if not tweet_id.isdigit():
        return None

    cache_file: Path = tweet_cache_path(tweet_id)
    if cache_file.is_file():
        return cache_file

    downloads_dir: Path = data_dir() / "Twitter" / "Downloads"
    for legacy_file in downloads_dir.glob(f"*/{tweet_id}.json"):
        if legacy_file.is_file():
            return legacy_file
    return None


def read_tweet_cache(cache_file: Path) -> dict[str, Any] | None:
    """Read cached tweet JSON, returning None if the file is missing or unusable.

    Args:
        cache_file: The cache file to read.

    Returns:
        The cached tweet JSON, or None when it cannot be used.
    """
    try:
        cached: Any = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Could not read cached tweet {cache_file}: {e}")
        return None
    if not isinstance(cached, dict):
        logger.warning(f"Cached tweet {cache_file} was not a JSON object")
        return None
    return cached


def save_tweet_cache(tweet_id: str, json_data: dict[str, Any]) -> None:
    """Write raw fxtwitter JSON to disk so it can be served and reused."""
    if not tweet_id.isdigit():
        return

    cache_file: Path = tweet_cache_path(tweet_id)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(json_data, ensure_ascii=False), encoding="utf-8")


def apply_tweet_translation(json_data: dict[str, Any]) -> dict[str, Any]:
    """Replace the status text with its translation when fxtwitter supplies one.

    Args:
        json_data: The tweet JSON to update.

    Returns:
        The tweet JSON with any translation applied to the status text.
    """
    status: dict[str, Any] = json_data.get("status") or {}
    translation: dict[str, Any] = status.get("translation") or {}
    translated_text: str = str(translation.get("text") or "")
    if translated_text:
        status["text"] = translated_text
        json_data["status"] = status
    return json_data


def wants_cache_bust(request: Request) -> bool:
    """Return True when the URL has any query string, e.g. a cache-busting token."""
    return bool(request.query_params)


def get_tweet(tweet_id: str, *, force_refresh: bool = False) -> dict[str, Any]:
    """Return tweet data from disk when available, otherwise fetch it from fxtwitter.

    Args:
        tweet_id: The status ID to look up.
        force_refresh: Ignore the cache and overwrite it with a fresh API response.

    Returns:
        The tweet JSON, with any translation applied to the status text.

    Raises:
        TypeError: If fxtwitter returns JSON that is not an object.
    """
    if not force_refresh:
        cache_file: Path | None = find_tweet_cache(tweet_id)
        if cache_file is not None:
            cached_data: dict[str, Any] | None = read_tweet_cache(cache_file)
            if cached_data is not None:
                logger.info(f"Tweet already downloaded: {tweet_id}")
                return apply_tweet_translation(cached_data)

    logger.info(f"Downloading tweet: {tweet_id}")
    api_url: str = f"https://api.fxtwitter.com/2/status/{tweet_id}?about_account=1&lang=en"
    response: niquests.Response = niquests.get(
        api_url,
        headers={
            "user-agent": "https://github.com/TheLovinator1/e.lovinator.space",
        },
    )
    response.raise_for_status()

    json_data: Any = response.json()
    if not isinstance(json_data, dict):
        msg = f"fxtwitter returned invalid JSON for {tweet_id}"
        raise TypeError(msg)

    save_tweet_cache(tweet_id, json_data)
    return apply_tweet_translation(json_data)


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


_MAX_EMBED_VIDEO_BYTES: int = 100 * 1024 * 1024
"""Largest video Discord reliably embeds. Bigger files fall back to a plain preview."""

_MAX_EMBED_VIDEO_DIMENSION: int = 1920
"""Largest video dimension Discord reliably plays. 4K uploads work but waste bandwidth."""

_MAX_MEDIA_URL_LENGTH: int = 2048
"""Longest URL Discord accepts for an unfurled media item."""

_VIDEO_DIMENSIONS_RE: re.Pattern[str] = re.compile(r"/(\d{2,5})x(\d{2,5})/")


def _video_dimensions(url: str, default: tuple[int, int]) -> tuple[int, int]:
    """Read ``<width>x<height>`` from a Twitter video URL.

    Args:
        url: A Twitter video URL such as ``.../vid/avc1/1280x720/clip.mp4``.
        default: Dimensions to use when the URL does not contain them.

    Returns:
        The parsed width and height, or ``default``.
    """
    match: re.Match[str] | None = _VIDEO_DIMENSIONS_RE.search(url)
    if match:
        return int(match.group(1)), int(match.group(2))
    return default


def select_video_rendition(video: dict[str, Any]) -> tuple[str, int, int]:  # ruff: ignore[too-many-locals]
    """Pick a video rendition that Discord can fetch, measure, and embed.

    fxtwitter's top-level ``url`` always points at the highest-quality MP4. For a long
    4K video that file can be over a gigabyte, which Discord refuses to embed: it has to
    measure the video within its 10 second link-preview budget, and large files fall back
    to a plain thumbnail. The ``formats`` list exposes smaller MP4 renditions, so pick the
    best one that still fits Discord's size budget.

    Args:
        video: A video object from the fxtwitter API.

    Returns:
        A ``(url, width, height)`` tuple. Falls back to the top-level URL when no usable
        MP4 renditions are listed.
    """
    default_width: int = safe_int(video.get("width"), 1280)
    default_height: int = safe_int(video.get("height"), 720)
    default_url: str = str(video.get("url") or "")
    duration: float = safe_float(video.get("duration"))

    renditions: dict[str, tuple[str, int, int, float]] = {}
    for fmt in video.get("formats") or []:
        if not isinstance(fmt, dict):
            continue

        url: str = str(fmt.get("url") or "")
        if urlparse(url).scheme not in {"http", "https"}:
            continue
        if str(fmt.get("container") or "").lower() != "mp4":
            continue

        width, height = _video_dimensions(url, (0, 0))
        if not width or not height:
            continue

        bitrate: int = safe_int(fmt.get("bitrate"))
        estimated_size: float = duration * bitrate / 8 if duration > 0 and bitrate > 0 else 0.0
        renditions[url] = (url, width, height, estimated_size)

    if not renditions:
        width, height = _video_dimensions(default_url, (default_width, default_height))
        return default_url, width, height

    candidates: list[tuple[str, int, int, float]] = list(renditions.values())
    known_sizes: list[tuple[str, int, int, float]] = [item for item in candidates if item[3] > 0]

    # Prefer the highest resolution that fits, capping 4K renditions (they play fine but
    # are usually too big for Discord to measure in time).
    if known_sizes:
        within_size_limit: list[tuple[str, int, int, float]] = [
            item for item in known_sizes if item[3] <= _MAX_EMBED_VIDEO_BYTES
        ]
        if within_size_limit:
            playable: list[tuple[str, int, int, float]] = [
                item for item in within_size_limit if max(item[1], item[2]) <= _MAX_EMBED_VIDEO_DIMENSION
            ]
            pool: list[tuple[str, int, int, float]] = playable or within_size_limit
            url, width, height, _ = max(pool, key=lambda item: item[1] * item[2])
            return url, width, height

        # Even the smallest rendition is over budget; send the least-bad one.
        url, width, height, _ = min(known_sizes, key=lambda item: (item[3], item[1] * item[2]))
        return url, width, height

    playable = [item for item in candidates if max(item[1], item[2]) <= _MAX_EMBED_VIDEO_DIMENSION]
    url, width, height, _ = max(playable or candidates, key=lambda item: item[1] * item[2])
    return url, width, height


def _format_size(num_bytes: float) -> str:
    """Format a byte count as megabytes or gigabytes.

    Args:
        num_bytes: The size in bytes.

    Returns:
        A human-readable size string.
    """
    if num_bytes >= 1024**3:
        return f"{num_bytes / 1024**3:.1f} GB"
    return f"{num_bytes / 1024**2:.0f} MB"


def describe_video_quality(video: dict[str, Any], url: str, width: int, height: int) -> str:
    """Describe how the embedded rendition compares to the original on X.

    Args:
        video: The original video object from the fxtwitter API.
        url: The rendition URL that is embedded.
        width: The embedded rendition's width.
        height: The embedded rendition's height.

    Returns:
        A warning line for display under the tweet, or an empty string when the embed
        already uses the original resolution.
    """
    source_width: int = safe_int(video.get("width"))
    source_height: int = safe_int(video.get("height"))
    if not source_width or not source_height:
        return ""
    if source_width <= width and source_height <= height:
        return ""

    duration: float = safe_float(video.get("duration"))
    source_bitrate: int = 0
    embedded_bitrate: int = 0
    for fmt in video.get("formats") or []:
        if not isinstance(fmt, dict) or str(fmt.get("container") or "").lower() != "mp4":
            continue
        bitrate: int = safe_int(fmt.get("bitrate"))
        if fmt.get("url") == url:
            embedded_bitrate = bitrate
        source_bitrate = max(source_bitrate, bitrate)

    source_size: float = duration * source_bitrate / 8 if duration > 0 and source_bitrate > 0 else 0.0

    def size_suffix(size: float) -> str:
        return f" ({_format_size(size)})" if size > 0 else ""

    improvements: list[str] = []
    pixel_ratio: float = (source_width * source_height) / (width * height)
    if pixel_ratio > 1.0:
        improvements.append(f"{pixel_ratio:.1f}× the pixels")  # ruff: ignore[ambiguous-unicode-character-string]
    if source_bitrate > embedded_bitrate > 0:
        improvements.append(f"{source_bitrate / embedded_bitrate:.1f}× the bitrate")  # ruff: ignore[ambiguous-unicode-character-string]

    better: str = f" - {' and '.join(improvements)}" if improvements else ""
    return f"⚠️ Higher quality on X: {source_width}×{source_height}{size_suffix(source_size)}{better}."  # ruff: ignore[ambiguous-unicode-character-string]


_COMPONENT_EMBED_SAFE_BYTES: int = 2800
"""Payload target that leaves headroom below Discord's 3,000-byte limit."""


def _discord_component_embed_size(payload: dict[str, Any]) -> int:
    """Return the HTML-safe, compact JSON payload size in UTF-8 bytes."""
    serialized: str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Match the escaping performed by Jinja's ``tojson`` filter.
    for char, escaped in (
        ("&", r"\u0026"),
        ("<", r"\u003c"),
        (">", r"\u003e"),
        ("'", r"\u0027"),
    ):
        serialized = serialized.replace(char, escaped)
    return len(serialized.encode("utf-8"))


def build_discord_component_embed(  # ruff: ignore[too-many-arguments]
    *,
    author_name: str,
    text: str,
    description: str,
    username: str,
    images: list[Photo],
    video: Video | None,
    quality_note: str = "",
    json_url: str = "",
) -> dict[str, Any]:
    """Build a Discord Component Embed for an X post.

    Uses a text display, an optional action row with a JSON download button,
    and a media gallery. Video media is represented as a gallery item so Discord
    can show a preview frame with playback controls.

    The payload is kept below Discord's 3,000-byte limit.

    Returns:
        The Discord component embed payload.
    """
    media_items: list[dict[str, Any]] = []

    if video:
        media_url: str = video["url"]
        if urlparse(media_url).scheme in {"http", "https"} and len(media_url) <= _MAX_MEDIA_URL_LENGTH:
            media_items.append({
                "media": {"url": media_url},
                "description": f"Video by @{username}"[:200],
            })
    else:
        for index, photo in enumerate(images[:10], start=1):
            media_url = photo["url"]
            if urlparse(media_url).scheme not in {"http", "https"} or len(media_url) > _MAX_MEDIA_URL_LENGTH:
                continue

            media_items.append({
                "media": {"url": media_url},
                "description": f"Photo {index} of {len(images)} by @{username}"[:200],
            })

    heading: str = author_name[:200]
    post_text: str = text[:1400]
    engagement: str = description[:250]

    def make_payload() -> dict[str, Any]:
        profile_url: str = f"https://x.com/{quote(username, safe='')}"
        content_parts: list[str] = [
            f"## [{heading} (@{username})]({profile_url})",
        ]
        if post_text:
            content_parts.append(post_text)
        if engagement:
            content_parts.append(engagement)
        components: list[dict[str, Any]] = [
            {
                "type": 10,
                "content": "\n\n".join(content_parts),
            },
        ]
        if json_url:
            components.append({
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 5,
                        "url": json_url,
                        "label": "Download JSON",
                    },
                ],
            })

        if quality_note:
            components.extend(({"type": 14}, {"type": 10, "content": quality_note}))

        if media_items:
            components.append({
                "type": 12,
                "items": media_items,
            })

        return {
            "component": {
                "type": 17,
                "accent_color": 0x26A7DE,
                "components": components,
            },
        }

    payload: dict[str, Any] = make_payload()

    # Leave headroom for any JSON escaping differences between serializers.
    while _discord_component_embed_size(payload) > _COMPONENT_EMBED_SAFE_BYTES:
        if media_items:
            media_items.pop()
        elif post_text:
            post_text = post_text[: max(0, len(post_text) - 100)]
        elif engagement:
            engagement = engagement[: max(0, len(engagement) - 50)]
        elif heading:
            heading = heading[: max(0, len(heading) - 50)]
        else:
            break
        payload = make_payload()

    return payload


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
    raw_text: dict[str, Any] = status.get("raw_text") or {}
    facets: list[dict[str, Any]] = raw_text.get("facets") or []

    content: str = convert_urls_to_links(text, facets).replace("\n", "<br>\u200a\u200a")
    stats_html: str = get_emoji_poop(status, html=True)
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
            video_url, final_w, final_h = select_video_rendition(video)
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
async def twitter(  # ruff: ignore[too-many-locals]
    request: Request,
    username: Annotated[str, PathParameter()],
    tweet_id: Annotated[str, PathParameter()],
) -> Response | Redirect:
    """Serve an Open Graph embed for a tweet to Discord clients.

    Returns:
        A redirect to the tweet URL for non-Discord clients, or a rendered
        HTML page with Open Graph metadata for Discord clients.
    """
    client_ip: IPv4Address | IPv6Address | None = get_client_ip(request)
    if client_ip is None or not await is_discord_client(client_ip):
        return Redirect(f"https://x.com/{username}/status/{tweet_id}", status_code=302)

    try:
        json_data: dict[str, Any] = get_tweet(tweet_id=tweet_id, force_refresh=wants_cache_bust(request))
    except niquests.HTTPError as e:
        logger.error(f"Failed to fetch tweet: {tweet_id}", exc_info=e)
        return Redirect(f"https://x.com/{username}/status/{tweet_id}", status_code=302)

    status: dict[str, Any] = json_data.get("status") or {}
    author: dict[str, Any] = json_data.get("author") or {}
    media: dict[str, Any] = status.get("media") or {}

    # Lock everything exactly to API screen_name so JSON & HTML match perfectly
    screen_name: str = str(author.get("screen_name") or username)
    tweet_url: str = f"https://x.com/{screen_name}/status/{tweet_id}"
    local_url: str = f"https://e.lovinator.space/{screen_name}/status/{tweet_id}"
    json_url: str = f"{str(request.base_url).rstrip('/')}/twitter/json/{quote(screen_name, safe='')}/{tweet_id}"

    logger.info(f"Serving tweet embed: {tweet_id}")

    text: str = str(status.get("text") or "").strip()
    title: str = text[:200]
    emoji_poop: str = get_emoji_poop(status, html=False)

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
    quality_note: str = ""
    if videos := media.get("videos"):
        item: dict[str, Any] = videos[0]
        if item.get("url"):
            video_url, video_width, video_height = select_video_rendition(item)
            quality_note = describe_video_quality(item, video_url, video_width, video_height)

            video = {
                "id": str(item.get("id") or "0"),
                "url": video_url,
                "preview_url": str(item.get("thumbnail_url") or ""),
                "duration": safe_float(item.get("duration")),
                "width": video_width,
                "height": video_height,
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

    about_account: dict[str, Any] = author.get("about_account") or {}
    based_in: str = str(about_account.get("based_in", "")).strip()
    source: str = str(about_account.get("source", "")).strip()
    footer: str = " • ".join(filter(None, (based_in, source))) or "e.lovinator.space"

    discord_component_embed: dict[str, Any] = build_discord_component_embed(
        author_name=name,
        text=text,
        description=emoji_poop,
        username=screen_name,
        images=photos,
        video=video,
        quality_note=quality_note,
        json_url=json_url,
    )

    response = Template(
        template_name="tweet.html",
        context={
            "stats": stats,
            "footer": footer,
            "name": name,
            "avatar": str(author.get("avatar_url") or ""),
            "images": photos,
            "video": video,
            "quality_note": quality_note,
            "poster": poster,
            "width": int(video["width"]) if video else 1280,
            "height": int(video["height"]) if video else 720,
            "og_type": og_type,
            "twitter_handle": f"@{screen_name}",
            "tweet_url": tweet_url,
            "json_url": json_url,
            "username": screen_name,
            "tweet_id": tweet_id,
            "title": title,
            "description": emoji_poop,
            "url": local_url,
            "site": f"@{screen_name}",
            "creator": f"@{screen_name}",
            "activity_url": f"https://e.lovinator.space/users/{screen_name}/statuses/{tweet_id}",
            "oembed_url": f"https://e.lovinator.space/_oembed/{screen_name}/{tweet_id}",
            "discord_component_embed": discord_component_embed,
        },
    )

    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@get("/twitter/json/{username:str}/{tweet_id:str}")
async def tweet_json_file(  # ruff: ignore[unused-async]
    request: Request,
    username: Annotated[str, PathParameter()],
    tweet_id: Annotated[str, PathParameter()],
) -> File:
    """Serve a tweet's cached fxtwitter JSON as a file download.

    Fetches the tweet first if it isn't cached yet (or if the URL has any query
    string), so the download button always has a file to serve.

    Args:
        request: The incoming request.
        username: The Twitter/X handle, used only to make the URL readable.
        tweet_id: The status ID of the tweet.

    Returns:
        The saved JSON file as an attachment.

    Raises:
        NotFoundException: If the tweet cannot be fetched or no cache file exists.
    """
    try:
        get_tweet(tweet_id=tweet_id, force_refresh=wants_cache_bust(request))
    except niquests.HTTPError as e:
        logger.error(f"Failed to fetch tweet JSON: {tweet_id}", exc_info=e)
        msg = f"Tweet {tweet_id} not found"
        raise NotFoundException(msg) from e

    cache_file: Path | None = find_tweet_cache(tweet_id)
    if cache_file is None:
        msg = f"No cached JSON for tweet {tweet_id}"
        raise NotFoundException(msg)

    logger.info(f"Serving tweet JSON: {username}/{tweet_id}")
    return File(path=cache_file, media_type="application/json", filename=f"{tweet_id}.json")


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
        json_data: dict[str, Any] = get_tweet(tweet_id=tweet_id, force_refresh=wants_cache_bust(request))
    except niquests.HTTPError:
        return Response(content={"error": "Not found"}, status_code=404, media_type="application/json")

    payload = build_mastodon_status(tweet_id, json_data)
    return Response(content=payload, media_type="application/json")


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
        json_data: dict[str, Any] = get_tweet(tweet_id=tweet_id, force_refresh=wants_cache_bust(request))
    except niquests.HTTPError:
        return Response(content={"error": "Not found"}, status_code=404, media_type="application/json")

    payload = build_mastodon_status(tweet_id, json_data)
    return Response(content=payload, media_type="application/json")


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
        oembed_data: dict[str, Any] = get_tweet(tweet_id=tweet_id, force_refresh=wants_cache_bust(request))
    except niquests.HTTPError:
        return Response(content={"error": "Not found"}, status_code=404, media_type="application/json")

    status: dict[str, Any] = oembed_data.get("status") or {}

    # Renders as old text at top of embed.
    # Use for engagement stats, reply indicators, or any primary label.
    # This OVERRIDES the Mastodon account.display_name.
    # TODO(TheLovinator): Add reply indicators: "↪ Replying to @another_user"  # ruff: ignore[missing-todo-link]
    # TODO(TheLovinator): Add Thread indicator: "↪ Thread by @user"  # ruff: ignore[missing-todo-link]
    author_name: str = get_emoji_poop(status, html=False)

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
        content=payload,
        media_type="application/json",
    )
