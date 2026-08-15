"""Application settings and shared filesystem locations."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from platformdirs import PlatformDirs

if TYPE_CHECKING:
    from pathlib import Path

DIRS: PlatformDirs = PlatformDirs(
    appauthor="TheLovinator",
    appname="e",
    ensure_exists=True,
    roaming=True,
)
"""Platform-specific directories for this application."""

DATA_DIR: Path = DIRS.user_data_path
"""Base directory for all persisted application data."""

MEDIA_DIR: Path = DATA_DIR / "Twitter" / "Downloads"
"""Directory gallery-dl downloads tweet media into."""

ARCHIVE_PATH: Path = DATA_DIR / "twitter.sqlite3"
"""gallery-dl download archive database."""

NITTER_INSTANCE: str = os.getenv("NITTER_INSTANCE", "https://nitter.net").rstrip("/")
"""Nitter instance used to fetch tweets.

Must be an instance supported by gallery-dl's Nitter extractor, e.g.
``nitter.net``, ``xcancel.com``, ``lightbrd.com`` or ``nitter.tiekoetter.com``.
"""

ORIGINAL_URL: str = os.getenv("ORIGINAL_URL", "https://twitter.com").rstrip("/")
"""Where non-Discord visitors are redirected."""

MEDIA_ROUTE: str = "/media"
"""Route prefix under which downloaded media files are served."""

MEDIA_DIR.mkdir(parents=True, exist_ok=True)
