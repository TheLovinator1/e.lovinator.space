import json
from base64 import b64encode
from datetime import UTC
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from gallery_dl.extractor.message import Message
from litestar.testing import TestClient

import e.twitter as twitter_module
from e.discord import DiscordIPs
from e.main import app
from e.twitter import Embed
from e.twitter import Media
from e.twitter import build_embed
from e.twitter import content_type_for
from e.twitter import extract_data
from e.twitter import generate_html
from e.twitter import is_media_file
from e.twitter import list_media_files
from e.twitter import original_image_url
from e.twitter import original_image_urls
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


def test_extract_data() -> None:
    """Test extracting tweet metadata and media from DataJob output."""
    job_data = [
        (Message.Directory, KWDICT),
        (
            Message.Url,
            "https://nitter.net/pic/1.jpg",
            {"filename": "1.jpg", "extension": "jpg", "num": 1},
        ),
    ]

    meta, media = extract_data(job_data)

    assert meta == KWDICT
    assert media == [
        {
            "url": "https://nitter.net/pic/1.jpg",
            "filename": "1.jpg",
            "extension": "jpg",
            "num": 1,
        },
    ]


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


def test_original_image_url() -> None:
    """Test reconstructing Twitter CDN URLs from Nitter media URLs."""
    assert (
        original_image_url("https://nitter.net/pic/orig/media%2FHPN4YF0X0AAx-ug.jpg")
        == "https://pbs.twimg.com/media/HPN4YF0X0AAx-ug.jpg"
    )
    assert (
        original_image_url("https://nitter.net/pic/orig/media%2FHPN4YF0X0AAx-ug.jpg?format=jpg&name=orig")
        == "https://pbs.twimg.com/media/HPN4YF0X0AAx-ug.jpg"
    )
    assert (
        original_image_url("https://nitter.net/pic/orig/media/HPN4YF0X0AAx-ug.png")
        == "https://pbs.twimg.com/media/HPN4YF0X0AAx-ug.png"
    )
    encoded = b64encode(b"https://pbs.twimg.com/media/HPN4YF0X0AAx-ug.jpg").decode()
    assert (
        original_image_url(f"https://nitter.net/pic/enc/{encoded}") == "https://pbs.twimg.com/media/HPN4YF0X0AAx-ug.jpg"
    )
    assert (
        original_image_url("https://pbs.twimg.com/media/HPN4YF0X0AAx-ug.jpg")
        == "https://pbs.twimg.com/media/HPN4YF0X0AAx-ug.jpg"
    )
    assert original_image_url("") is None
    assert original_image_url("https://nitter.net/video/abc") is None
    assert original_image_url("ytdl:https://nitter.net/i/status/123") is None


def test_original_image_urls() -> None:
    """Test reconstructing URLs for a tweet's image media items."""
    items = [
        {"url": "https://nitter.net/pic/orig/media%2Fa.jpg", "extension": "jpg"},
        {"url": "https://nitter.net/pic/orig/media%2Fb.png", "extension": "png"},
    ]
    assert original_image_urls(items) == [
        "https://pbs.twimg.com/media/a.jpg",
        "https://pbs.twimg.com/media/b.png",
    ]

    # Video items are skipped.
    mixed = [
        {"url": "ytdl:https://nitter.net/i/status/123", "extension": "mp4"},
        {"url": "https://nitter.net/pic/orig/media%2Fa.jpg", "extension": "jpg"},
    ]
    assert original_image_urls(mixed) == ["https://pbs.twimg.com/media/a.jpg"]

    # A non-reconstructable image makes the whole result None.
    broken = [{"url": "https://nitter.net/weird-url", "extension": "jpg"}]
    assert original_image_urls(broken) is None

    assert original_image_urls([]) == []
    assert original_image_urls([{"url": "ytdl:https://nitter.net/i/status/123", "extension": "mp4"}]) == []


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


def test_build_embed_with_image_urls() -> None:
    """Test building an embed from external Twitter CDN image URLs."""
    embed = build_embed(
        KWDICT,
        [],
        base_url="https://e.lovinator.space",
        canonical_url="https://twitter.com/DiscussingFilm/status/2086143411984208230",
        image_urls=("https://pbs.twimg.com/media/HPN4YF0X0AAx-ug.jpg",),
    )

    assert embed.media[0].url == "https://pbs.twimg.com/media/HPN4YF0X0AAx-ug.jpg"
    assert embed.media[0].content_type == "image/jpeg"
    assert not embed.media[0].is_video


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

    assert 'name="twitter:card" content="player"' in rendered
    assert 'property="og:video" content="https://e.lovinator.space/media/1.mp4"' in rendered
    assert 'property="og:video:type" content="video/mp4"' in rendered
    assert 'property="og:video:width" content="1280"' in rendered
    assert 'property="og:video:height" content="720"' in rendered
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


def test_download_archives_metadata_and_returns_files(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that download archives metadata and returns the media files."""

    class FakePathfmt:
        directory = str(tmp_dir)

    class FakeDataJob:
        def __init__(self, url: str, *, file: object = None) -> None:
            self.exception = None
            self.data = [
                (Message.Directory, KWDICT),
                (
                    Message.Url,
                    "https://nitter.net/pic/1.jpg",
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

    monkeypatch.setattr(twitter_module.job, "DataJob", FakeDataJob)
    monkeypatch.setattr(twitter_module.job, "DownloadJob", FakeDownloadJob)
    (tmp_dir / "1.jpg").write_bytes(b"")

    result = twitter_module.download("https://nitter.net/DiscussingFilm/status/2086143411984208230")

    assert result is not None
    meta, files = result
    assert meta["author"]["name"] == "DiscussingFilm"
    assert [path.name for path in files] == ["1.jpg"]

    metadata_path = tmp_dir / "metadata.json"
    assert metadata_path.exists()

    archived = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert archived["author"]["name"] == "DiscussingFilm"
    assert archived["media"] == [
        {
            "url": "https://nitter.net/pic/1.jpg",
            "num": 1,
            "filename": "1.jpg",
            "extension": "jpg",
        },
    ]
    assert archived["files"][0]["filename"] == "1.jpg"
    assert archived["files"][0]["content_type"] == "image/jpeg"


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

    async def fake_fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return KWDICT, []

    def fake_download_media(nitter_url: str) -> tuple[Path | None, list[Path]]:
        return None, [Path("1.mp4")]

    monkeypatch.setattr(twitter_module, "fetch_meta_async", fake_fetch_meta)
    monkeypatch.setattr(twitter_module, "download_media", fake_download_media)

    with TestClient(app=app) as client:
        response = client.get("/DiscussingFilm/status/2086143411984208230")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'name="twitter:player:stream"' in response.text
    assert "/media/1.mp4" in response.text


def test_route_video_uses_direct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that a video embed points at Nitter's direct MP4 immediately."""
    monkeypatch.setattr(
        twitter_module,
        "client_ip_from",
        lambda request: ip_address("127.0.0.1"),
    )

    direct_url = (
        "https://nitter.net/video/abc/https%3A%2F%2Fvideo.twimg.com%2F"
        "amplify_video%2F1%2Fvid%2Favc1%2F1080x1920%2Fabc.mp4"
    )

    async def fake_fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return KWDICT, [{"url": direct_url, "extension": "mp4", "num": 1}]

    calls: dict[str, str] = {}

    def fake_download_background(nitter_url: str, meta: dict[str, Any], media_items: list[dict[str, Any]]) -> None:
        calls["url"] = nitter_url

    monkeypatch.setattr(twitter_module, "fetch_meta_async", fake_fetch_meta)
    monkeypatch.setattr(twitter_module, "download_background", fake_download_background)

    with TestClient(app=app) as client:
        response = client.get("/DiscussingFilm/status/2086143411984208230")

    assert response.status_code == 200
    assert f'property="og:video" content="{direct_url}"' in response.text
    assert 'property="og:video:width" content="1080"' in response.text
    assert 'property="og:video:height" content="1920"' in response.text
    assert calls["url"].endswith("/DiscussingFilm/status/2086143411984208230")


def test_route_image_uses_original_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that an image embed points at Twitter's CDN immediately."""
    monkeypatch.setattr(
        twitter_module,
        "client_ip_from",
        lambda request: ip_address("127.0.0.1"),
    )

    media_items = [
        {"url": "https://nitter.net/pic/orig/media%2FHPN4YF0X0AAx-ug.jpg", "extension": "jpg", "num": 1},
        {"url": "https://nitter.net/pic/orig/media%2FHPN4YF0X0AAx-ug2.jpg", "extension": "jpg", "num": 2},
    ]

    async def fake_fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return KWDICT, media_items

    calls: dict[str, str] = {}

    def fake_download_background(nitter_url: str, meta: dict[str, Any], media_items: list[dict[str, Any]]) -> None:
        calls["url"] = nitter_url

    def fake_download_media(nitter_url: str) -> tuple[Path | None, list[Path]]:
        msg = "download_media should not run when images are embedded directly"
        raise AssertionError(msg)

    monkeypatch.setattr(twitter_module, "fetch_meta_async", fake_fetch_meta)
    monkeypatch.setattr(twitter_module, "download_background", fake_download_background)
    monkeypatch.setattr(twitter_module, "download_media", fake_download_media)

    with TestClient(app=app) as client:
        response = client.get("/DiscussingFilm/status/2086143411984208230")

    assert response.status_code == 200
    assert 'property="og:image" content="https://pbs.twimg.com/media/HPN4YF0X0AAx-ug.jpg"' in response.text
    assert 'property="og:image" content="https://pbs.twimg.com/media/HPN4YF0X0AAx-ug2.jpg"' in response.text
    assert "testserver.local/media/" not in response.text
    assert calls["url"].endswith("/DiscussingFilm/status/2086143411984208230")


def test_route_image_falls_back_to_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that an unreconstructable image URL falls back to downloading."""
    monkeypatch.setattr(
        twitter_module,
        "client_ip_from",
        lambda request: ip_address("127.0.0.1"),
    )

    media_items = [{"url": "https://nitter.net/weird-url", "extension": "jpg", "num": 1}]

    async def fake_fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return KWDICT, media_items

    def fake_download_media(nitter_url: str) -> tuple[Path | None, list[Path]]:
        return None, [Path("1.jpg")]

    monkeypatch.setattr(twitter_module, "fetch_meta_async", fake_fetch_meta)
    monkeypatch.setattr(twitter_module, "download_media", fake_download_media)

    with TestClient(app=app) as client:
        response = client.get("/DiscussingFilm/status/2086143411984208230")

    assert response.status_code == 200
    assert 'property="og:image" content="http://testserver.local/media/1.jpg"' in response.text


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
