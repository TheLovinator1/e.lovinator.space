import json
import re
from dataclasses import dataclass
from ipaddress import IPv4Address
from ipaddress import IPv4Network
from ipaddress import IPv6Address
from ipaddress import ip_address
from typing import TYPE_CHECKING
from typing import Any

import niquests
from gallery_dl import config
from gallery_dl import job
from litestar import Request
from litestar import get
from litestar.response import Redirect
from loguru import logger
from platformdirs import PlatformDirs

from e.discord import DiscordIPs
from e.discord import Prefix
from e.discord import Service
from e.discord import get_discord_ips

if TYPE_CHECKING:
    from pathlib import Path

    from litestar.datastructures import Address

import anyio
from anyio import to_thread

dirs: PlatformDirs = PlatformDirs(
    appauthor="TheLovinator",
    appname="e",
    ensure_exists=True,
    roaming=True,
)

DATADIR: Path = dirs.user_data_path
ARCHIVE_PATH: Path = DATADIR / "twitter.sqlite3"
BASE_DIRECTORY: Path = DATADIR / "Twitter" / "Downloads"

BASE_DIRECTORY.mkdir(parents=True, exist_ok=True)

MAX_CONCURRENT_DOWNLOADS = 8
REQUEST_TIMEOUT = 30


@dataclass(frozen=True, slots=True)
class Download:
    """A file to download."""

    url: str
    path: Path


def sanitize_path_component(value: str) -> str:
    """Make a string safe to use as a single filesystem path component."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = value.strip(" .")

    return value or "unknown"


def configure_extractor() -> None:
    """Configure the Nitter extractor."""
    config.load()

    config.set(
        path=("extractor",),
        key="base-directory",
        value=BASE_DIRECTORY,
    )
    config.set(
        path=("extractor", "nitter"),
        key="quoted",
        value=True,
    )
    config.set(
        path=("extractor", "nitter"),
        key="retweets",
        value=True,
    )
    config.set(
        path=("extractor", "nitter"),
        key="directory",
        value=["{author['name']}", "{tweet_id}"],
    )
    config.set(
        path=("extractor", "nitter"),
        key="filename",
        value="{num}.{extension}",
    )
    config.set(
        path=("extractor",),
        key="archive",
        value=str(ARCHIVE_PATH),
    )
    config.set(
        path=("extractor",),
        key="archive-pragma",
        value=["journal_mode=WAL", "synchronous=NORMAL"],
    )
    config.set(
        path=("extractor", "nitter"),
        key="postprocessors",
        value=[
            {
                "name": "metadata",
                "mode": "json",
            },
        ],
    )


def extract_data(
    job_data: list[Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extract tweet metadata and media from extractor output."""
    try:
        meta = next(item[1] for item in job_data if item[0] == 2)
    except StopIteration as exc:
        msg = "Extractor returned no tweet metadata"
        raise RuntimeError(msg) from exc

    media_items = [
        {
            "url": item[1],
            **item[2],
        }
        for item in job_data
        if item[0] == 3
    ]

    return meta, media_items


def create_downloads(
    media_items: list[dict[str, Any]],
    target_dir: Path,
) -> list[Download]:
    """Create download targets for extracted media."""
    downloads: list[Download] = []

    for index, item in enumerate(media_items, start=1):
        extension = str(item["extension"]).lstrip(".").lower()
        number = item.get("num", index)

        path = target_dir / f"{number}.{extension}"

        downloads.append(
            Download(
                url=str(item["url"]),
                path=path,
            ),
        )

    return downloads


async def download_file(
    session: niquests.AsyncSession,
    download: Download,
    limiter: anyio.CapacityLimiter,
) -> None:
    """Download one file."""
    async with limiter:
        logger.info(
            "Downloading %s to %s",
            download.url,
            download.path,
        )

        response: niquests.Response = await session.get(
            download.url,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        content: bytes | None = response.content
        if content is None:
            msg: str = f"No content received from {download.url}"
            raise RuntimeError(msg)

        await anyio.Path(download.path).write_bytes(content)


async def download_files(
    downloads: list[Download],
) -> None:
    """Download files concurrently with bounded concurrency."""
    limiter = anyio.CapacityLimiter(MAX_CONCURRENT_DOWNLOADS)

    async with niquests.AsyncSession() as session, anyio.create_task_group() as task_group:
        for download in downloads:
            task_group.start_soon(
                download_file,
                session,
                download,
                limiter,
            )


async def write_metadata(
    path: Path,
    metadata: dict[str, Any],
) -> None:
    """Write metadata JSON."""
    content = json.dumps(
        metadata,
        default=str,
        indent=2,
        ensure_ascii=False,
    )

    await anyio.Path(path).write_text(
        content,
        encoding="utf-8",
    )


async def download(url: str) -> Path | None:
    """Download a tweet and its media."""
    configure_extractor()

    data_job = job.DataJob(url)

    # DataJob.run() is synchronous, so don't block the event loop.
    await to_thread.run_sync(data_job.run)

    job_data = data_job.data

    if not job_data:
        logger.warning("No data returned for %s", url)
        return None

    meta, media_items = extract_data(job_data)

    author = meta.get("author", {}).get("name") or meta.get("user", {}).get("name") or "unknown"
    author = sanitize_path_component(str(author))

    tweet_id = sanitize_path_component(str(meta["tweet_id"]))

    target_dir = BASE_DIRECTORY / author / tweet_id
    target_dir.mkdir(parents=True, exist_ok=True)

    downloads = create_downloads(media_items, target_dir)

    await download_files(downloads)

    downloaded_files = [
        {
            "url": download.url,
            "filename": download.path.name,
            "path": str(download.path.resolve()),
        }
        for download in downloads
    ]

    meta["files"] = downloaded_files

    json_path = target_dir / "metadata.json"
    await write_metadata(json_path, meta)

    return json_path


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
    ips.prefixes.append(Prefix(ipv4Prefix=IPv4Network("127.0.0.1"), services=[Service("api"), Service("media")]))

    client_ip: IPv4Address | IPv6Address = ip_address(client.host)

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

    tweet = download(url=nitter_url)

    logger.info(tweet)

    return {
        "url": f"https://twitter.com/{username}/status/{tweet_id}",
        "nitter_url": nitter_url,
        "e_url": str(request.url),
        "username": username,
        "tweet_id": tweet_id,
    }
