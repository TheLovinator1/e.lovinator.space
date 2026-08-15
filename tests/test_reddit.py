from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from gallery_dl import config
from gallery_dl.extractor.message import Message
from litestar.testing import TestClient

import e.reddit as reddit_module
import e.twitter as twitter_module
from e.discord import DiscordIPs
from e.main import app
from e.reddit import build_embed
from e.settings import REDDIT_CLIENT_ID
from e.settings import REDDIT_USER_AGENT

if TYPE_CHECKING:
    import pytest

REDDIT_META: dict[str, Any] = {
    "subreddit": "aww",
    "title": "Cute puppy",
    "selftext": "Look at this good boy",
    "author": "someone",
    "id": "abc123",
}


def test_configure_extractor_uses_oauth() -> None:
    """Test that the Reddit extractor is configured for OAuth authentication."""
    reddit_module.configure_extractor()

    assert config.get(("extractor", "reddit"), "client-id") == REDDIT_CLIENT_ID
    assert config.get(("extractor", "reddit"), "user-agent") == REDDIT_USER_AGENT


def test_configure_extractor_uses_refresh_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that a refresh token from the environment is applied."""
    monkeypatch.setattr(reddit_module, "REDDIT_REFRESH_TOKEN", "test-token")

    reddit_module.configure_extractor()

    assert config.get(("extractor", "reddit"), "refresh-token") == "test-token"


def test_build_embed(tmp_dir: Path) -> None:
    """Test building an embed from Reddit metadata and files."""
    image = tmp_dir / "1.jpg"
    image.write_bytes(b"")

    embed = build_embed(
        REDDIT_META,
        [image],
        base_url="https://e.lovinator.space",
        canonical_url="https://www.reddit.com/r/aww/comments/abc123",
        media_root=tmp_dir,
    )

    assert embed.title == "Cute puppy"
    assert embed.description == "Look at this good boy"
    assert embed.url == "https://www.reddit.com/r/aww/comments/abc123"
    assert embed.media[0].url == "https://e.lovinator.space/media/1.jpg"
    assert embed.media[0].content_type == "image/jpeg"


def test_download_archives_metadata_and_returns_files(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that download archives metadata and returns the media files."""

    class FakePathfmt:
        directory = str(tmp_dir)

    class FakeDataJob:
        def __init__(self, url: str, *, file: object = None) -> None:
            self.exception = None
            self.data = [
                (Message.Directory, REDDIT_META),
                (
                    Message.Url,
                    "https://i.redd.it/abc.jpg",
                    {"filename": "1.jpg", "extension": "jpg", "num": 1},
                ),
            ]

        def run(self) -> int:
            return 0

    class FakeDownloadJob:
        def __init__(self, url: str) -> None:
            self.pathfmt = FakePathfmt()

        def run(self) -> int:
            return 0

    monkeypatch.setattr(reddit_module.job, "DataJob", FakeDataJob)
    monkeypatch.setattr(reddit_module.job, "DownloadJob", FakeDownloadJob)
    (tmp_dir / "1.jpg").write_bytes(b"")

    result = reddit_module.download("https://www.reddit.com/r/aww/comments/abc123")

    assert result is not None
    meta, files = result
    assert meta["title"] == "Cute puppy"
    assert [path.name for path in files] == ["1.jpg"]

    metadata_path = tmp_dir / "metadata.json"
    assert metadata_path.exists()

    archived = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert archived["title"] == "Cute puppy"
    assert archived["media"][0]["url"] == "https://i.redd.it/abc.jpg"
    assert archived["files"][0]["filename"] == "1.jpg"


def _empty_ips() -> DiscordIPs:
    return DiscordIPs(
        creationTime=datetime(2026, 8, 4, tzinfo=UTC),
        syncToken="test",
        notes="test",
        prefixes=[],
    )


def test_route_returns_embed_for_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that Discord clients receive an Open Graph embed page."""
    monkeypatch.setattr(
        reddit_module,
        "client_ip_from",
        lambda request: ip_address("127.0.0.1"),
    )

    async def fake_download_async(reddit_url: str) -> tuple[dict[str, Any], list[Path]]:  # ruff: ignore[unused-async]
        return REDDIT_META, [Path("1.jpg")]

    monkeypatch.setattr(reddit_module, "download_async", fake_download_async)

    with TestClient(app=app) as client:
        response = client.get("/r/aww/comments/abc123")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'property="og:image"' in response.text
    assert "/media/1.jpg" in response.text


def test_route_with_slug_returns_embed_for_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that a post URL with a slug is also served to Discord clients."""
    monkeypatch.setattr(
        reddit_module,
        "client_ip_from",
        lambda request: ip_address("127.0.0.1"),
    )

    async def fake_download_async(reddit_url: str) -> tuple[dict[str, Any], list[Path]]:  # ruff: ignore[unused-async]
        return REDDIT_META, [Path("1.jpg")]

    monkeypatch.setattr(reddit_module, "download_async", fake_download_async)

    with TestClient(app=app) as client:
        response = client.get("/r/aww/comments/abc123/cute_puppy")

    assert response.status_code == 200
    assert 'property="og:image"' in response.text


def test_route_redirects_non_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that non-Discord clients are redirected to the original post."""
    monkeypatch.setattr(
        reddit_module,
        "client_ip_from",
        lambda request: ip_address("203.0.113.5"),
    )

    async def fake_get_discord_ips() -> DiscordIPs:  # ruff: ignore[unused-async]
        return _empty_ips()

    monkeypatch.setattr(twitter_module, "get_discord_ips", fake_get_discord_ips)

    with TestClient(app=app) as client:
        response = client.get(
            "/r/aww/comments/abc123/cute_puppy/",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "https://www.reddit.com/r/aww/comments/abc123"
