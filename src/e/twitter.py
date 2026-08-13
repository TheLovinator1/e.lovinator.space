from ipaddress import ip_address
from typing import TYPE_CHECKING
from typing import Any
from typing import TypedDict

import wreq
from htpy import head
from htpy import html
from htpy import meta
from litestar import Request
from litestar import get
from litestar.response import Redirect
from loguru import logger
from selectolax.parser import HTMLParser
from selectolax.parser import Node
from wreq import Client
from wreq import Emulation

from e.discord import DiscordIPs
from e.discord import Prefix
from e.discord import get_discord_ips

if TYPE_CHECKING:
    from htpy._types import Renderable
    from litestar.datastructures import Address


STAT_FIELDS: dict[str, str] = {
    "icon-comment": "comments",
    "icon-retweet": "retweets",
    "icon-heart": "likes",
    "icon-views": "views",
}

type Stats = dict[str, int | None]


class TweetAuthor(TypedDict):
    """Author metadata for a tweet."""

    name: str | None
    username: str | None
    profile_url: str | None
    avatar: str | None
    verified: bool


class TweetDate(TypedDict):
    """Date metadata for a tweet."""

    relative: str | None
    published: str | None
    title: str | None
    url: str | None


class TweetMedia(TypedDict):
    """Media item attached to a tweet."""

    url: str | None
    thumbnail: str | None
    width: int | None
    height: int | None
    type: str | None


class Tweet(TypedDict):
    """Structure of a tweet object."""

    author: TweetAuthor
    date: TweetDate
    text: str | None
    media: list[TweetMedia]
    stats: Stats


def generate_html(tweet: Tweet) -> Renderable:
    """Generate HTML for a tweet.

    Args:
        tweet: The tweet to generate HTML for.

    Returns:
        The HTML for the tweet.
    """
    # Discord supports oEmbed, Open Graph, and Twitter Card metadata formats for rendering link embeds.
    meta_tags = [
        meta(property="theme-color", content="#1d9bf0"),
    ]

    # loop through all the images and add as og:image
    meta_tags.extend(
        meta(
            property="og:image",
            content=image["thumbnail"],
            width=image["width"],
            height=image["height"],
            type=image["type"],
            secure_url=image["thumbnail"],
            alt=tweet["text"],
        )
        for image in tweet["media"]
    )

    return html[
        head[
            meta(name="viewport", content="width=device-width, initial-scale=1.0"),
            *meta_tags,
        ]
    ]


def save_data_to_disk(tweet: dict) -> None:
    """Save tweet data to DATA_DIR/twitter/<username>/<tweet_id>/data.json.

    Has version number so we can update the file later if data changes.

    Args:
        tweet: The tweet to save.

    """


def text(node: Node | None, default: str | None = None) -> str | None:
    """Safely extract text from a node.

    Args:
        node: The node to extract text from.
        default: The default value to return if the node is None.

    Returns:
        The text of the node, or the default value if the node is None.
    """
    return node.text(strip=True) if node else default


def attr(node: Node | None, name: str, default: str | None = None) -> str | None:
    """Safely extract an attribute from a node.

    Args:
        node: The node to extract the attribute from.
        name: The name of the attribute.
        default: The default value to return if the node is None.

    Returns:
        The attribute of the node, or the default value if the node is None.
    """
    return node.attributes.get(name, default) if node else default


def parse_number(value: str) -> int | None:
    """Convert '46,391' -> 46391, '1.5K' -> 1500.

    Args:
        value: The value to convert.

    Returns:
        The converted value.
    """
    if not value:
        logger.warning("Value is empty.")
        return None

    value = value.replace(",", "").strip()

    if value.endswith(("K", "k")):
        try:
            return int(float(value[:-1]) * 1_000)
        except ValueError:
            pass
    elif value.endswith(("M", "m")):
        try:
            return int(float(value[:-1]) * 1_000_000)
        except ValueError:
            pass

    try:
        return int(value)
    except ValueError:
        logger.error("Could not convert value '{}' to int.", value)
        return None


def parse_stats(tweet: Node) -> dict[str, int | None]:
    """Parse the stats of a tweet.

    Args:
        tweet: The tweet to parse the stats from.

    Returns:
        The stats of the tweet.
    """
    stats = {}

    for stat in tweet.css(".tweet-stat"):
        icon = stat.css_first("span[class*='icon-']")
        if not icon:
            logger.warning("Could not find icon for stat.")
            continue

        classes = attr(icon, "class", "")
        if not classes:
            logger.warning("Could not find classes for icon.")
            continue

        field = next(
            (field for icon_class, field in STAT_FIELDS.items() if icon_class in classes),
            None,
        )
        if not field:
            logger.warning("Could not find field for icon classes.")
            continue

        # Extract stat value by removing icon element
        icon.decompose()
        value = stat.text(strip=True)

        num = parse_number(value)
        if num is None:
            logger.warning("Could not parse number for stat.")
            continue

        stats[field] = num

    return stats


def parse_tweet(html: str) -> Tweet:
    """Parse a tweet from HTML.

    Args:
        html: The HTML of the tweet.

    Returns:
        The parsed tweet.

    Raises:
        ValueError: If .tweet-body cannot be found.
    """
    tree = HTMLParser(html)

    tweet = tree.css_first(".main-tweet .tweet-body") or tree.css_first(".tweet-body")

    if not tweet:
        msg = "Could not find .tweet-body"
        raise ValueError(msg)

    # Author
    avatar: Node | None = tweet.css_first(".tweet-avatar img")
    fullname: Node | None = tweet.css_first(".fullname")
    username: Node | None = tweet.css_first(".username")

    # Date
    date_link: Node | None = tweet.css_first(".tweet-date a")
    published: Node | None = tweet.css_first(".tweet-published")

    # Text
    content: Node | None = tweet.css_first(".tweet-content")

    # Media
    media: list[TweetMedia] = []

    for attachment in tweet.css(".attachments .attachment"):
        link: Node | None = attachment.css_first("a")
        image: Node | None = attachment.css_first("img")
        video: Node | None = attachment.css_first("video")
        source: Node | None = attachment.css_first("video source") or attachment.css_first("source")

        if video or source:
            media.append({
                "url": attr(source, "src") or attr(video, "src"),
                "thumbnail": attr(video, "poster"),
                "width": None,
                "height": None,
                "type": attr(source, "type"),
            })
        elif link or image:
            media.append({
                "url": attr(link, "href") or attr(image, "src"),
                "thumbnail": attr(image, "src") or attr(link, "href"),
                "width": None,
                "height": None,
                "type": None,
            })

    return {
        "author": {
            "name": text(fullname),
            "username": text(username),
            "profile_url": attr(username, "href"),
            "avatar": attr(avatar, "src"),
            "verified": bool(tweet.css_first(".verified-icon")),
        },
        "date": {
            "relative": text(date_link),
            "published": text(published),
            "title": attr(date_link, "title"),
            "url": attr(date_link, "href"),
        },
        "text": text(content),
        "media": media,
        "stats": parse_stats(tweet),
    }


@get("/{username:str}/status/{tweet_id:str}")
async def twitter(request: Request, username: str, tweet_id: str) -> dict[str, Any] | Redirect:
    """Handle Twitter requests.

    https://twitter.com/DiscussingFilm/status/2086143411984208230
    https://x.com/DiscussingFilm/status/2086143411984208230
    https://nitter.net/DiscussingFilm/status/2086143411984208230

    https://e.lovinator.space/DiscussingFilm/status/2086143411984208230

    If IP is from Discord:
        Download the image/video.
        Return custom HTML with metadata tags with the image/video.

    Otherwise:
        Redirect to the original URL.

    Args:
        request: The request.
        username: The Twitter handle.
        tweet_id: The status ID of the tweet.

    Returns:
        Redirect to the original URL, or custom HTML with metadata tags.

    Raises:
        ValueError: If client address is missing.
    """
    logger.info(f"Request for {request.url!r} from {request.client}")
    logger.info("Username: {}, Tweet ID: {}", username, tweet_id)

    client: Address | None = request.client
    if client is None:
        msg = "No client address"
        raise ValueError(msg)

    ips: DiscordIPs = await get_discord_ips()

    # Append ["127.0.0.1"] for local testing.
    ips.prefixes.append(Prefix(ipv4_prefix="127.0.0.1", services=["api", "media"]))

    client_ip = ip_address(client.host)

    for ip in ips.prefixes:
        if client_ip in ip.ipv4_prefix:
            logger.info("Client IP {} is in Discord IPs", client.host)
            break
    else:
        logger.warning("Client IP {} is not in Discord IPs", client.host)
        return Redirect(
            path=f"https://twitter.com/{username}/status/{tweet_id}",
            status_code=302,
        )

    logger.info("Client IP {} is in Discord IPs", client.host)

    nitter_url = f"https://nitter.net/{username}/status/{tweet_id}"
    logger.info("Getting tweet from Nitter: {}", nitter_url)

    wreq_client = Client(emulation=Emulation.Chrome149)
    resp: wreq.Response = await wreq_client.get(nitter_url)
    data: str = await resp.text()
    logger.info("Got tweet from Nitter: {}", data)

    tweet = parse_tweet(data)

    logger.info(tweet)

    return {
        "url": f"https://twitter.com/{username}/status/{tweet_id}",
        "nitter_url": nitter_url,
        "e_url": str(request.url),
        "username": username,
        "tweet_id": tweet_id,
        "media": tweet["media"],
        "author": tweet["author"],
        "date": tweet["date"],
        "text": tweet["text"],
        "stats": tweet["stats"],
    }
