import json
from base64 import b64encode
from datetime import UTC
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import TextIO

from gallery_dl.extractor.message import Message
from litestar.testing import TestClient

import e.twitter as twitter_module
from e.discord import DiscordIPs
from e.main import app
from e.twitter import Embed
from e.twitter import Media
from e.twitter import avatar_from_profile
from e.twitter import build_embed
from e.twitter import compact_number
from e.twitter import content_type_for
from e.twitter import extract_data
from e.twitter import generate_activity_html
from e.twitter import generate_html
from e.twitter import is_media_file
from e.twitter import list_media_files
from e.twitter import original_image_url
from e.twitter import original_image_urls
from e.twitter import public_url

if TYPE_CHECKING:
    import pytest

KWDICT: dict[str, Any] = {
    "author": {"name": "DiscussingFilm", "nick": "DiscussingFilm"},
    "tweet_id": "2086143411984208230",
    "retweets": 1234,
    "likes": 34567,
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


def test_compact_number() -> None:
    """Test compact count formatting."""
    assert compact_number(914) == "914"
    assert compact_number(25301) == "25.3K"
    assert compact_number(1249683) == "1.2M"
    assert compact_number(1000000) == "1M"
    assert compact_number(0) == "0"
    assert compact_number(-5) == "-5"


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
    assert embed.site == "@DiscussingFilm"
    assert embed.creator == "@DiscussingFilm"
    assert embed.stats == (("Retweets", "1.2K"), ("Likes", "34.6K"))

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


def test_generate_html_author_and_stats() -> None:
    """Test that author handles and stats render as Twitter card tags."""
    embed = Embed(
        title="DiscussingFilm (@DiscussingFilm)",
        description="A video.",
        url="https://twitter.com/DiscussingFilm/status/2086143411984208230",
        media=(),
        site="@DiscussingFilm",
        creator="@DiscussingFilm",
        stats=(("Retweets", "1.2K"), ("Likes", "34.6K"), ("Quotes", "3")),
    )

    rendered = generate_html(embed)

    assert 'name="twitter:site" content="@DiscussingFilm"' in rendered
    assert 'name="twitter:creator" content="@DiscussingFilm"' in rendered
    assert 'name="twitter:label1" content="Retweets"' in rendered
    assert 'name="twitter:data1" content="1.2K"' in rendered
    assert 'name="twitter:label2" content="Likes"' in rendered
    assert 'name="twitter:data2" content="34.6K"' in rendered
    assert "twitter:label3" not in rendered


def test_avatar_from_profile() -> None:
    """Test extracting the avatar from a Nitter profile page."""
    page = (
        '<img class="avatar round" src="https://pbs.twimg.com/profile_images/'
        '1706429397467549696/hmvwfChQ_bigger.jpg" alt="" loading="lazy" />'
    )

    assert avatar_from_profile(page) == "https://pbs.twimg.com/profile_images/1706429397467549696/hmvwfChQ_400x400.jpg"
    assert avatar_from_profile("<html><body></body></html>") is None


def test_avatar_from_profile_absolutizes_relative_url() -> None:
    """Test that a proxied avatar path is resolved against the Nitter root."""
    page = '<img class="avatar" src="/pic/profile_images/abc_bigger.jpg" />'

    assert avatar_from_profile(page) == "https://nitter.net/pic/profile_images/abc_400x400.jpg"


def test_generate_activity_html_video_embed() -> None:
    """Test that the Mastodon-style head links the activity documents."""
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
        site="@DiscussingFilm",
        creator="@DiscussingFilm",
    )

    rendered = generate_activity_html(
        embed,
        activity_url="https://e.lovinator.space/users/DiscussingFilm/statuses/2086143411984208230",
        oembed_url="https://e.lovinator.space/_oembed/DiscussingFilm/2086143411984208230",
    )

    assert 'rel="alternate" type="application/activity+json"' in rendered
    assert 'rel="alternate" type="application/json+oembed"' in rendered
    assert 'property="twitter:site" content="@DiscussingFilm"' in rendered
    assert 'property="twitter:creator" content="@DiscussingFilm"' in rendered
    assert 'property="twitter:player:stream" content="https://e.lovinator.space/media/1.mp4"' in rendered
    assert 'property="og:video" content="https://e.lovinator.space/media/1.mp4"' in rendered
    assert 'name="twitter:card" content="player"' in rendered
    assert 'name="twitter:image" content="0"' in rendered
    assert 'rel="canonical" href="https://twitter.com/DiscussingFilm/status/2086143411984208230"' in rendered
    assert 'property="theme-color" content="#1d9bf0"' in rendered
    assert 'rel="icon" href="/favicon.ico"' in rendered
    assert 'rel="apple-touch-icon" href="/apple-touch-icon.png"' in rendered
    assert "og:image" not in rendered
    assert "og:video:secure_url" not in rendered
    assert "twitter:label" not in rendered


def test_generate_activity_html_text_only_uses_avatar() -> None:
    """Test that text-only posts use the author avatar as the image."""
    embed = Embed(
        title="DiscussingFilm (@DiscussingFilm)",
        description="Just text.",
        url="https://twitter.com/DiscussingFilm/status/2086143411984208230",
        media=(),
    )

    rendered = generate_activity_html(
        embed,
        activity_url="https://e.lovinator.space/users/DiscussingFilm/statuses/2086143411984208230",
        oembed_url="https://e.lovinator.space/_oembed/DiscussingFilm/2086143411984208230",
        avatar_url="https://example.com/avatar.jpg",
    )

    assert 'property="og:image" content="https://example.com/avatar.jpg"' in rendered
    assert 'name="twitter:image" content="0"' in rendered
    assert 'name="twitter:card" content="summary"' in rendered
    assert 'rel="canonical" href="https://twitter.com/DiscussingFilm/status/2086143411984208230"' in rendered


def test_download_archives_metadata_and_returns_files(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that download archives metadata and returns the media files."""

    class FakePathfmt:
        directory = str(tmp_dir)

    class FakeDataJob:
        def __init__(self, url: str, *, file: TextIO | None = None) -> None:
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


async def _fake_avatar(username: str) -> str:  # ruff: ignore[unused-async]
    """Return a fixed avatar URL for the author."""
    return "https://example.com/avatar.jpg"


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
    monkeypatch.setattr(twitter_module, "avatar_url_for", _fake_avatar)

    with TestClient(app=app) as client:
        response = client.get("/DiscussingFilm/status/2086143411984208230")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'property="twitter:player:stream"' in response.text
    assert 'property="twitter:site" content="@DiscussingFilm"' in response.text
    assert 'rel="alternate" type="application/activity+json"' in response.text
    assert 'rel="alternate" type="application/json+oembed"' in response.text
    assert 'href="/favicon.ico"' in response.text
    assert "/media/1.mp4" in response.text
    assert "twitter:label" not in response.text


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
    monkeypatch.setattr(twitter_module, "avatar_url_for", _fake_avatar)

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
    monkeypatch.setattr(twitter_module, "avatar_url_for", _fake_avatar)

    with TestClient(app=app) as client:
        response = client.get("/DiscussingFilm/status/2086143411984208230")

    assert response.status_code == 200
    # The Mastodon-style head suppresses og:image so Discord keeps the activity card.
    assert "og:image" not in response.text
    assert 'name="twitter:card" content="summary_large_image"' in response.text
    assert 'rel="alternate" type="application/activity+json"' in response.text
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
    monkeypatch.setattr(twitter_module, "avatar_url_for", _fake_avatar)

    with TestClient(app=app) as client:
        response = client.get("/DiscussingFilm/status/2086143411984208230")

    assert response.status_code == 200
    # The Mastodon-style head suppresses og:image so Discord keeps the activity card.
    assert "og:image" not in response.text
    assert 'rel="alternate" type="application/activity+json"' in response.text


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


def test_route_en_translates_tweet_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the /en route serves a translated embed."""
    monkeypatch.setattr(
        twitter_module,
        "client_ip_from",
        lambda request: ip_address("127.0.0.1"),
    )

    async def fake_fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return KWDICT, []

    def fake_download_media(nitter_url: str) -> tuple[Path | None, list[Path]]:
        return None, [Path("1.mp4")]

    async def fake_translate_embed(embed: Embed, fields: tuple[str, ...]) -> Embed:  # ruff: ignore[unused-async]
        assert fields == ("description",)
        return Embed(title=embed.title, description="Ryan Hurst shared a photo.", url=embed.url, media=embed.media)

    monkeypatch.setattr(twitter_module, "fetch_meta_async", fake_fetch_meta)
    monkeypatch.setattr(twitter_module, "download_media", fake_download_media)
    monkeypatch.setattr(twitter_module, "translate_embed", fake_translate_embed)
    monkeypatch.setattr(twitter_module, "avatar_url_for", _fake_avatar)

    with TestClient(app=app) as client:
        response = client.get("/DiscussingFilm/status/2086143411984208230/en")

    assert response.status_code == 200
    assert 'property="og:description" content="Ryan Hurst shared a photo."' in response.text
    assert 'rel="alternate" type="application/json+oembed"' in response.text


def test_route_en_redirects_non_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that non-Discord clients are redirected even on /en routes."""
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
            "/DiscussingFilm/status/2086143411984208230/en",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == ("https://twitter.com/DiscussingFilm/status/2086143411984208230")


def test_route_serves_activity_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the activity+json route returns a Mastodon Status document."""
    monkeypatch.setattr(twitter_module, "avatar_url_for", _fake_avatar)

    async def fake_fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return dict(KWDICT), []

    monkeypatch.setattr(twitter_module, "fetch_meta_async", fake_fetch_meta)

    with TestClient(app=app) as client:
        response = client.get("/users/DiscussingFilm/statuses/2086143411984208230")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    payload = response.json()
    assert payload["id"] == "2086143411984208230"
    assert payload["url"] == "https://twitter.com/DiscussingFilm/status/2086143411984208230"
    assert payload["uri"] == payload["url"]
    assert "🔁 1.2K&ensp;❤️ 34.6K" in payload["content"]
    assert payload["content"].startswith("Ryan Hurst has shared a photo")
    assert payload["content"].endswith("<br><br><b>🔁 1.2K&ensp;❤️ 34.6K</b>")
    # The Nitter HTML is stripped to plain text (no relative links).
    assert "<a href" not in payload["content"]
    assert payload["application"] == {"name": "Twitter", "website": None}
    assert "replies_count" not in payload
    assert "reblogs_count" not in payload
    assert "favourites_count" not in payload
    assert payload["account"]["acct"] == "DiscussingFilm"
    assert payload["account"]["uri"] == "https://twitter.com/DiscussingFilm/status/2086143411984208230"
    assert payload["account"]["avatar"] == "https://example.com/avatar.jpg"
    assert payload["account"]["url"] == "https://twitter.com/DiscussingFilm/status/2086143411984208230"
    assert "header" not in payload["account"]


def test_route_serves_api_v1_status_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the Mastodon REST endpoint serves a Status document by ID."""
    monkeypatch.setattr(twitter_module, "avatar_url_for", _fake_avatar)

    meta = {
        **KWDICT,
        "author": {"name": "noa_mpfentame", "nick": "のあ🫧MPF☆Bみずいろ担当💎"},
    }

    async def fake_fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        # The Nitter extractor resolves statuses by ID alone.
        assert nitter_url == "https://nitter.net/i/status/2088615278074900973"
        return dict(meta), []

    monkeypatch.setattr(twitter_module, "fetch_meta_async", fake_fetch_meta)

    with TestClient(app=app) as client:
        response = client.get("/api/v1/statuses/2088615278074900973")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["id"] == "2088615278074900973"
    assert payload["url"] == "https://twitter.com/noa_mpfentame/status/2088615278074900973"
    # The handle comes from author.name; the display name from author.nick.
    assert payload["account"]["acct"] == "noa_mpfentame"
    assert payload["account"]["username"] == "noa_mpfentame"
    assert payload["account"]["display_name"] == "のあ🫧MPF☆Bみずいろ担当💎"
    assert payload["account"]["url"] == "https://twitter.com/noa_mpfentame/status/2088615278074900973"
    assert payload["account"]["uri"] == payload["account"]["url"]


def test_route_activity_document_includes_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the activity document carries image and video attachments."""
    monkeypatch.setattr(twitter_module, "avatar_url_for", _fake_avatar)

    media_items = [
        {"url": "https://nitter.net/pic/orig/media%2Fa.jpg", "extension": "jpg", "num": 1},
        {"url": "https://nitter.net/video/abc.mp4", "extension": "mp4", "num": 2},
    ]

    async def fake_fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return dict(KWDICT), media_items

    monkeypatch.setattr(twitter_module, "fetch_meta_async", fake_fetch_meta)

    with TestClient(app=app) as client:
        response = client.get("/users/DiscussingFilm/statuses/2086143411984208230")

    assert response.status_code == 200
    payload = response.json()
    assert payload["media_attachments"] == [
        {
            "id": "0",
            "type": "image",
            "url": "https://pbs.twimg.com/media/a.jpg",
            "preview_url": "https://pbs.twimg.com/media/a.jpg",
            "remote_url": None,
            "preview_remote_url": None,
            "text_url": None,
            "description": None,
        },
        {
            "id": "1",
            "type": "video",
            "url": "https://nitter.net/video/abc.mp4",
            "preview_url": None,
            "remote_url": None,
            "preview_remote_url": None,
            "text_url": None,
            "description": None,
        },
    ]


def test_account_document_omits_missing_images() -> None:
    """Test that image fields are omitted when there is no avatar."""
    document = twitter_module._account_document(
        KWDICT,
        "DiscussingFilm",
        None,
        "2025-01-01T00:00:00.000Z",
        "https://twitter.com/DiscussingFilm/status/2086143411984208230",
    )

    assert "avatar" not in document
    assert "avatar_static" not in document
    assert "header" not in document
    assert "header_static" not in document
    assert document["acct"] == "DiscussingFilm"


def test_account_document_includes_avatar_when_present() -> None:
    """Test that the avatar fields are emitted when an avatar is known."""
    document = twitter_module._account_document(
        KWDICT,
        "DiscussingFilm",
        "https://example.com/avatar.jpg",
        "2025-01-01T00:00:00.000Z",
        "https://twitter.com/DiscussingFilm/status/2086143411984208230",
    )

    assert document["avatar"] == "https://example.com/avatar.jpg"
    assert document["avatar_static"] == "https://example.com/avatar.jpg"


def test_route_serves_oembed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the oEmbed route returns counts in the author name."""

    async def fake_fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return dict(KWDICT), []

    monkeypatch.setattr(twitter_module, "fetch_meta_async", fake_fetch_meta)

    with TestClient(app=app) as client:
        response = client.get("/_oembed/DiscussingFilm/2086143411984208230")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    payload = response.json()
    assert payload["type"] == "rich"
    assert payload["author_name"] == "🔁 1.2K   ❤️ 34.6K"
    assert payload["author_url"] == "https://twitter.com/DiscussingFilm/status/2086143411984208230"
    assert payload["provider_name"] == "e.lovinator.space"


def test_route_activity_document_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that a missing tweet returns 404 from the JSON routes."""

    async def fake_fetch_meta(nitter_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:  # ruff: ignore[unused-async]
        return None

    monkeypatch.setattr(twitter_module, "fetch_meta_async", fake_fetch_meta)

    with TestClient(app=app) as client:
        activity = client.get("/users/DiscussingFilm/statuses/999")
        api = client.get("/api/v1/statuses/999")
        oembed = client.get("/_oembed/DiscussingFilm/999")

    assert activity.status_code == 404
    assert api.status_code == 404
    assert oembed.status_code == 404
    assert api.json() == {"error": "Not Found"}
