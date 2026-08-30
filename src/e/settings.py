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

REDDIT_MEDIA_DIR: Path = DATA_DIR / "Reddit" / "Downloads"
"""Directory gallery-dl downloads Reddit media into."""

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

REDGIFS_MEDIA_DIR: Path = DATA_DIR / "Redgifs" / "Downloads"
"""Directory gallery-dl downloads Redgifs media into."""

REDGIFS_ARCHIVE_PATH: Path = DATA_DIR / "redgifs.sqlite3"
"""gallery-dl download archive database for Redgifs."""

REDGIFS_URL: str = os.getenv("REDGIFS_URL", "https://www.redgifs.com").rstrip("/")
"""Base URL of Redgifs, used to build links and redirects."""

MEDIA_ROUTE: str = "/media"
"""Route prefix under which downloaded media files are served."""

DEEPSEEK_API_KEY: str | None = os.getenv("DEEPSEEK_API_KEY")
"""DeepSeek API key used to translate embeds into English.

When unset, ``/en`` routes serve the untranslated text.
"""

DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
"""Base URL of the DeepSeek API (OpenAI-compatible)."""

DEEPSEEK_TRANSLATION_MODEL: str = os.getenv("DEEPSEEK_TRANSLATION_MODEL", "deepseek-v4-pro")
"""DeepSeek model used to translate embeds into English."""

TRANSLATIONS_PATH: Path = DATA_DIR / "translations.json"
"""On-disk cache of past translations, keyed by the source text."""

REDDIT_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
REDGIFS_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
