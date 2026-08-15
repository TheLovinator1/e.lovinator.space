from __future__ import annotations

import os
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from platformdirs import PlatformDirs

if TYPE_CHECKING:
    from pathlib import Path

load_dotenv()

DIRS: PlatformDirs = PlatformDirs(
    appauthor="TheLovinator",
    appname="e",
    ensure_exists=True,
    roaming=True,
)
"""Platform-specific directories for this application."""

DATA_DIR: Path = DIRS.user_data_path
"""Base directory for all persisted application data."""

TWITTER_MEDIA_DIR: Path = DATA_DIR / "Twitter" / "Downloads"
"""Directory gallery-dl downloads tweet media into."""

REDDIT_MEDIA_DIR: Path = DATA_DIR / "Reddit" / "Downloads"
"""Directory gallery-dl downloads Reddit media into."""

ARCHIVE_PATH: Path = DATA_DIR / "twitter.sqlite3"
"""gallery-dl download archive database."""

NITTER_INSTANCE: str = os.getenv("NITTER_INSTANCE", "https://nitter.net").rstrip("/")
"""Nitter instance used to fetch tweets.

Must be an instance supported by gallery-dl's Nitter extractor, e.g.
``nitter.net``, ``xcancel.com``, ``lightbrd.com`` or ``nitter.tiekoetter.com``.
"""

ORIGINAL_URL: str = os.getenv("ORIGINAL_URL", "https://twitter.com").rstrip("/")
"""Where non-Discord visitors are redirected."""

REDDIT_URL: str = os.getenv("REDDIT_URL", "https://www.reddit.com").rstrip("/")
"""Base URL of Reddit, used to build links and redirects."""

REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "yH0aTnJEt6qUgGn835B4vg")
"""Reddit OAuth client ID used by gallery-dl."""

REDDIT_USER_AGENT: str = os.getenv("REDDIT_USER_AGENT", "org.quantumbadger.redreader/1.25.1")
"""User-Agent sent with Reddit API requests."""

REDDIT_REFRESH_TOKEN: str | None = os.getenv("REDDIT_REFRESH_TOKEN")
"""Reddit OAuth refresh token.

When unset, gallery-dl falls back to reading the token from its cache.
"""

REDDIT_ARCHIVE_PATH: Path = DATA_DIR / "reddit.sqlite3"
"""gallery-dl download archive database for Reddit."""

MEDIA_ROUTE: str = "/media"
"""Route prefix under which downloaded media files are served."""

TWITTER_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
REDDIT_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
