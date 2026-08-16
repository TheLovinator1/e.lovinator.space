from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Self

from gallery_dl import config
from gallery_dl.extractor.message import Message
from litestar.testing import TestClient

import e.redgifs as redgifs_module
import e.twitter as twitter_module
from e.discord import DiscordIPs
from e.main import app
from e.redgifs import build_embed
from e.settings import REDGIFS_MEDIA_DIR
from e.twitter import Embed

if TYPE_CHECKING:
    import pytest

GIF_ID = "waterloggedmediumpurplequillback"

REDGIFS_META: dict[str, Any] = {
    "id": GIF_ID,
    "userName": "wobby89",
    "description": "A very nice gif",
    "tags": ["Asian", "Cowgirl"],
    "width": 1920,
    "height": 1080,
    "views": 1249683,
    "likes": 914,
    "category": "redgifs",
    "urls": {
        "poster": f"https://media.redgifs.com/{GIF_ID.capitalize()}-poster.jpg",
        "hd": f"https://media.redgifs.com/{GIF_ID.capitalize()}.mp4",
    },
}


def test_configure_extractor_uses_redgifs_settings() -> None:
    """Test that the Redgifs extractor is configured for the Redgifs dir."""
    redgifs_module.configure_extractor()

    assert config.get(path=("extractor",), key="base-directory") == str(REDGIFS_MEDIA_DIR)
    assert config.get(path=("extractor", "redgifs"), key="directory") == ["{category}", "{id}"]
    assert config.get(path=("extractor", "redgifs"), key="filename") == "{id}.{extension}"


def test_build_embed(tmp_dir: Path) -> None:
    """Test building an embed from gif metadata and files."""
    video = tmp_dir / "1.mp4"
    video.write_bytes(b"")

    embed = build_embed(
        REDGIFS_META,
        [video],
        base_url="https://e.lovinator.space",
        canonical_url=f"https://www.redgifs.com/watch/{GIF_ID}",
        media_root=tmp_dir,
    )

    assert embed.title == "wobby89 on Redgifs"
    assert embed.description == "A very nice gif\n\nTags: Asian, Cowgirl"
    assert embed.url == f"https://www.redgifs.com/watch/{GIF_ID}"
    assert embed.stats == (("Views", "1.2M"), ("Likes", "914"))
    assert embed.media[0].url == "https://e.lovinator.space/media/1.mp4"
    assert embed.media[0].content_type == "video/mp4"
    assert embed.media[0].width == 1920
    assert embed.media[0].height == 1080
    assert embed.poster == f"https://media.redgifs.com/{GIF_ID.capitalize()}-poster.jpg"


def test_build_embed_falls_back_to_tags_when_description_empty() -> None:
    """Test that the tags are used as the description when it is empty."""
    meta = {**REDGIFS_META, "description": ""}

    embed = build_embed(
        meta,
        [],
        base_url="https://e.lovinator.space",
        canonical_url=f"https://www.redgifs.com/watch/{GIF_ID}",
    )

    assert embed.description == "Asian, Cowgirl"


def test_build_embed_uses_description_when_no_tags() -> None:
    """Test that the description stands alone when there are no tags."""
    meta = {**REDGIFS_META, "tags": []}

    embed = build_embed(
        meta,
        [],
        base_url="https://e.lovinator.space",
        canonical_url=f"https://www.redgifs.com/watch/{GIF_ID}",
    )

    assert embed.description == "A very nice gif"


def test_build_embed_external_video_url() -> None:
    """Test that an embed can point at Redgifs' direct MP4 immediately."""
    embed = build_embed(
        REDGIFS_META,
        [],
        base_url="https://e.lovinator.space",
        canonical_url=f"https://www.redgifs.com/watch/{GIF_ID}",
        video_url=f"https://media.redgifs.com/{GIF_ID.capitalize()}.mp4",
    )

    assert embed.media[0].url == f"https://media.redgifs.com/{GIF_ID.capitalize()}.mp4"
    assert embed.media[0].content_type == "video/mp4"
    assert embed.media[0].width == 1920
    assert embed.media[0].height == 1080


def test_download_video_archives_metadata_and_returns_files(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that download archives metadata and returns the media files."""
    monkeypatch.setattr(redgifs_module, "REDGIFS_MEDIA_DIR", tmp_dir)

    class FakeDataJob:
        def __init__(self, url: str, *, file: object = None) -> None:
            self.exception = None
            self.data = [
                (Message.Directory, REDGIFS_META),
                (
                    Message.Url,
                    f"https://media.redgifs.com/{GIF_ID.capitalize()}.mp4",
                    {"filename": GIF_ID.capitalize(), "extension": "mp4", "num": 0},
                ),
            ]

        def run(self) -> int:
            return 0

    class FakeResponse:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def raise_for_status(self) -> None:
            pass

        def iter_content(self, chunk_size: int) -> list[bytes]:
            return [b"video", b"-bytes"]

    def fake_get(url: str, stream: bool, headers: dict[str, str]) -> FakeResponse:
        assert url == f"https://media.redgifs.com/{GIF_ID.capitalize()}.mp4"
        return FakeResponse()

    monkeypatch.setattr(redgifs_module.job, "DataJob", FakeDataJob)
    monkeypatch.setattr(redgifs_module.niquests, "get", fake_get)

    result = redgifs_module.download(f"https://www.redgifs.com/watch/{GIF_ID}")

    assert result is not None
    meta, files = result
    assert meta["userName"] == "wobby89"
    assert [path.name for path in files] == [f"{GIF_ID}.mp4"]
    assert files[0].read_bytes() == b"video-bytes"

    metadata_path = tmp_dir / "redgifs" / GIF_ID / "metadata.json"
    assert metadata_path.exists()

    archived = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert archived["userName"] == "wobby89"
    assert archived["media"][0]["url"] == f"https://media.redgifs.com/{GIF_ID.capitalize()}.mp4"
    assert archived["files"][0]["filename"] == f"{GIF_ID}.mp4"


def test_download_uses_gallery_dl_when_no_direct_mp4(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that download falls back to gallery-dl without a direct MP4."""

    class FakePathfmt:
        directory = str(tmp_dir)

    class FakeDataJob:
        def __init__(self, url: str, *, file: object = None) -> None:
            self.exception = None
            self.data = [
                (Message.Directory, REDGIFS_META),
                (
                    Message.Url,
                    "https://media.redgifs.com/some.gif",
                    {"filename": "some", "extension": "gif", "num": 0},
                ),
            ]

        def run(self) -> int:
            return 0

    class FakeDownloadJob:
        def __init__(self, url: str) -> None:
            self.pathfmt = FakePathfmt()

        def run(self) -> int:
            return 0

    monkeypatch.setattr(redgifs_module.job, "DataJob", FakeDataJob)
    monkeypatch.setattr(redgifs_module.job, "DownloadJob", FakeDownloadJob)
    (tmp_dir / "1.gif").write_bytes(b"")

    result = redgifs_module.download(f"https://www.redgifs.com/watch/{GIF_ID}")

    assert result is not None
    _, files = result
    assert [path.name for path in files] == ["1.gif"]

    metadata_path = tmp_dir / "metadata.json"
    assert metadata_path.exists()


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
        redgifs_module,
        "client_ip_from",
        lambda request: ip_address("127.0.0.1"),
    )

    async def fake_fetch_meta(redgifs_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return dict(REDGIFS_META), []

    async def fake_download_media(redgifs_url: str) -> tuple[Path | None, list[Path]]:  # ruff: ignore[unused-async]
        return None, [Path("1.mp4")]

    monkeypatch.setattr(redgifs_module, "fetch_meta_async", fake_fetch_meta)
    monkeypatch.setattr(redgifs_module, "download_media_async", fake_download_media)

    with TestClient(app=app) as client:
        response = client.get(f"/watch/{GIF_ID}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'property="og:title" content="wobby89 on Redgifs"' in response.text
    assert 'name="twitter:label1" content="Views"' in response.text
    assert 'name="twitter:data1" content="1.2M"' in response.text
    assert "/media/1.mp4" in response.text
    assert (
        'property="og:image" content="https://media.redgifs.com/Waterloggedmediumpurplequillback-poster.jpg"'
        in response.text
    )


def test_route_uses_direct_url_and_downloads_in_background(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that the embed serves Redgifs' direct MP4 and archives later."""
    monkeypatch.setattr(
        redgifs_module,
        "client_ip_from",
        lambda request: ip_address("127.0.0.1"),
    )
    monkeypatch.setattr(redgifs_module, "REDGIFS_MEDIA_DIR", tmp_dir)

    media_items = [
        {
            "url": f"https://media.redgifs.com/{GIF_ID.capitalize()}.mp4",
            "extension": "mp4",
            "filename": GIF_ID.capitalize(),
            "num": 0,
        },
    ]

    async def fake_fetch_meta(redgifs_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return dict(REDGIFS_META), media_items

    calls: dict[str, str] = {}

    def fake_background(video_url: str, target: Path, meta: dict[str, Any], media_items: list[dict[str, Any]]) -> None:
        calls["url"] = video_url

    monkeypatch.setattr(redgifs_module, "fetch_meta_async", fake_fetch_meta)
    monkeypatch.setattr(redgifs_module, "download_video_background", fake_background)

    with TestClient(app=app) as client:
        response = client.get(f"/watch/{GIF_ID}")

    assert response.status_code == 200
    assert (
        'property="og:video" content="https://media.redgifs.com/Waterloggedmediumpurplequillback.mp4"' in response.text
    )
    assert calls["url"] == f"https://media.redgifs.com/{GIF_ID.capitalize()}.mp4"


def test_route_redirects_non_discord(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that non-Discord clients are redirected to the original gif."""
    monkeypatch.setattr(
        redgifs_module,
        "client_ip_from",
        lambda request: ip_address("203.0.113.5"),
    )

    async def fake_get_discord_ips() -> DiscordIPs:  # ruff: ignore[unused-async]
        return _empty_ips()

    monkeypatch.setattr(twitter_module, "get_discord_ips", fake_get_discord_ips)

    with TestClient(app=app) as client:
        response = client.get(
            f"/watch/{GIF_ID}",
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == f"https://www.redgifs.com/watch/{GIF_ID}"


def test_route_en_translates_gif_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that the /en route serves a translated embed."""
    monkeypatch.setattr(
        redgifs_module,
        "client_ip_from",
        lambda request: ip_address("127.0.0.1"),
    )

    async def fake_fetch_meta(redgifs_url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # ruff: ignore[unused-async]
        return dict(REDGIFS_META), []

    async def fake_download_media(redgifs_url: str) -> tuple[Path | None, list[Path]]:  # ruff: ignore[unused-async]
        return None, [Path("1.mp4")]

    async def fake_translate_embed(embed: Embed, fields: tuple[str, ...]) -> Embed:  # ruff: ignore[unused-async]
        assert fields == ("title", "description")
        return Embed(title="Söt gif", description="En väldigt trevlig gif", url=embed.url, media=embed.media)

    monkeypatch.setattr(redgifs_module, "fetch_meta_async", fake_fetch_meta)
    monkeypatch.setattr(redgifs_module, "download_media_async", fake_download_media)
    monkeypatch.setattr(redgifs_module, "translate_embed", fake_translate_embed)

    with TestClient(app=app) as client:
        response = client.get(f"/watch/{GIF_ID}/en")

    assert response.status_code == 200
    assert 'property="og:title" content="Söt gif"' in response.text
    assert 'property="og:description" content="En väldigt trevlig gif"' in response.text
