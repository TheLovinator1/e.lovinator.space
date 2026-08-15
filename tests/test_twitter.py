from datetime import UTC
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from litestar.testing import TestClient

import e.twitter as twitter_module
from e.discord import DiscordIPs
from e.main import app
from e.twitter import Embed
from e.twitter import Media
from e.twitter import build_embed
from e.twitter import content_type_for
from e.twitter import generate_html
from e.twitter import is_media_file
from e.twitter import list_media_files
from e.twitter import public_url

if TYPE_CHECKING:
    import pytest

KWDICT: dict[str, Any] = {
    "author": {"name": "DiscussingFilm", "nick": "@DiscussingFilm"},
    "tweet_id": "2086143411984208230",
    "content": (
        'Ryan Hurst has shared <a href="/DiscussingFilm">a photo</a> of himself partially in makeup as Kratos.'
    ),
}


def test_content_type_for() -> None:
    """Test mapping of extensions to MIME types."""
    assert content_type_for("1.jpg") == "image/jpeg"
    assert content_type_for(Path("2.mp4")) == "video/mp4"
    assert content_type_for("3.webp") == "image/webp"
    assert content_type_for("unknown.bin") == "application/octet-stream"


def test_is_media_file(tmp_dir: Path) -> None:
    """Test that only supported media files are recognized."""
    (tmp_dir / "1.jpg").write_bytes(b"")
    (tmp_dir / "2.mp4").write_bytes(b"")
    (tmp_dir / "metadata.json").write_bytes(b"{}")

    assert is_media_file(tmp_dir / "1.jpg")
    assert is_media_file(tmp_dir / "2.mp4")
    assert not is_media_file(tmp_dir / "metadata.json")


def test_list_media_files_sorts_numerically(tmp_dir: Path) -> None:
    """Test that media files are returned in numeric order, ignoring others."""
    (tmp_dir / "10.jpg").write_bytes(b"")
    (tmp_dir / "2.jpg").write_bytes(b"")
    (tmp_dir / "1.jpg").write_bytes(b"")
    (tmp_dir / "metadata.json").write_bytes(b"{}")

    files = list_media_files(tmp_dir)

    assert [path.name for path in files] == ["1.jpg", "2.jpg", "10.jpg"]


def test_list_media_files_missing_directory(tmp_dir: Path) -> None:
    """Test that a missing directory yields an empty list."""
    assert list_media_files(tmp_dir / "does-not-exist") == []


def test_public_url(tmp_dir: Path) -> None:
    """Test building public URLs for downloaded media."""
    path = tmp_dir / "DiscussingFilm" / "2086143411984208230" / "1.mp4"

    assert (
        public_url(path, "https://e.lovinator.space", tmp_dir)
        == "https://e.lovinator.space/media/DiscussingFilm/2086143411984208230/1.mp4"
    )


def test_build_embed(tmp_dir: Path) -> None:
    """Test building an embed from gallery-dl metadata and files."""
    image = tmp_dir / "1.jpg"
    video = tmp_dir / "2.mp4"
    image.write_bytes(b"")
    video.write_bytes(b"")

    embed = build_embed(
        KWDICT,
        [image, video],
        base_url="https://e.lovinator.space",
        canonical_url="https://twitter.com/DiscussingFilm/status/2086143411984208230",
        media_root=tmp_dir,
    )

    assert embed.title == "DiscussingFilm (@DiscussingFilm)"
    assert embed.description == ("Ryan Hurst has shared a photo of himself partially in makeup as Kratos.")
    assert embed.url == "https://twitter.com/DiscussingFilm/status/2086143411984208230"

    assert embed.media[0].content_type == "image/jpeg"
    assert not embed.media[0].is_video
    assert embed.media[0].url == "https://e.lovinator.space/media/1.jpg"

    assert embed.media[1].content_type == "video/mp4"
    assert embed.media[1].is_video
    assert embed.media[1].url == "https://e.lovinator.space/media/2.mp4"


def test_generate_html_video_embed() -> None:
    """Test that a video embed renders og:video and player card tags."""
    embed = Embed(
        title="DiscussingFilm (@DiscussingFilm)",
        description="A video.",
        url="https://twitter.com/DiscussingFilm/status/2086143411984208230",
        media=(
            Media(
                url="https://e.lovinator.space/media/1.mp4",
                content_type="video/mp4",
            ),
        ),
    )

    rendered = generate_html(embed)

    assert 'property="og:video" content="https://e.lovinator.space/media/1.mp4"' in rendered
    assert 'property="og:video:type" content="video/mp4"' in rendered
    assert 'name="twitter:card" content="player"' in rendered
    assert 'name="twitter:player:stream" content="https://e.lovinator.space/media/1.mp4"' in rendered


def test_generate_html_image_embed() -> None:
    """Test that an image embed renders og:image and large image card tags."""
    embed = Embed(
        title="DiscussingFilm (@DiscussingFilm)",
        description="A photo.",
        url="https://twitter.com/DiscussingFilm/status/2086143411984208230",
        media=(
            Media(
                url="https://e.lovinator.space/media/1.jpg",
                content_type="image/jpeg",
            ),
        ),
    )

    rendered = generate_html(embed)

    assert 'property="og:image" content="https://e.lovinator.space/media/1.jpg"' in rendered
    assert 'name="twitter:card" content="summary_large_image"' in rendered
    assert "og:video" not in rendered


def test_download_returns_metadata_and_files(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that download runs gallery-dl and returns metadata and files."""

    class FakePathfmt:
        kwdict = KWDICT
        directory = str(tmp_dir)

    class FakeJob:
        def __init__(self, url: str) -> None:
            self.pathfmt = FakePathfmt()

        def run(self) -> int:
            return 0

    monkeypatch.setattr(twitter_module.job, "DownloadJob", FakeJob)
    (tmp_dir / "1.jpg").write_bytes(b"")

    result = twitter_module.download("https://nitter.net/DiscussingFilm/status/2086143411984208230")

    assert result is not None
    kwdict, files = result
    assert kwdict["author"]["name"] == "DiscussingFilm"
    assert [path.name for path in files] == ["1.jpg"]


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
        twitter_module,
        "client_ip_from",
        lambda request: ip_address("127.0.0.1"),
    )

    async def fake_download_async(nitter_url: str) -> tuple[dict[str, Any], list[Path]]:  # ruff: ignore[unused-async]
        return KWDICT, [Path("1.mp4")]

    monkeypatch.setattr(twitter_module, "download_async", fake_download_async)

    with TestClient(app=app) as client:
        response = client.get("/DiscussingFilm/status/2086143411984208230")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'property="og:video"' in response.text
    assert "/media/1.mp4" in response.text


def test_route_redirects_non_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that non-Discord clients are redirected to the original tweet."""
    monkeypatch.setattr(
        twitter_module,
        "client_ip_from",
        lambda request: ip_address("203.0.113.5"),
    )

    async def fake_get_discord_ips() -> DiscordIPs:  # ruff: ignore[unused-async]
        return _empty_ips()

    monkeypatch.setattr(twitter_module, "get_discord_ips", fake_get_discord_ips)

    with TestClient(app=app) as client:
        response = client.get(
            "/DiscussingFilm/status/2086143411984208230",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == ("https://twitter.com/DiscussingFilm/status/2086143411984208230")
