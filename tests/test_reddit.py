from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

import pytest
from gallery_dl import config
from litestar.testing import TestClient

from e.main import app
from e.reddit import download_reddit_video
from e.reddit import get_reddit_post
from e.reddit import get_subreddit_about
from e.reddit import reddit_media
from e.reddit import reddit_media_path

if TYPE_CHECKING:
    from pathlib import Path


def test_reddit_media_uses_hosted_gallery_images() -> None:
    """Gallery images use their Reddit CDN URLs without downloading."""
    post: dict[str, Any] = {
        "gallery_data": {"items": [{"media_id": "first"}]},
        "media_metadata": {
            "first": {"s": {"u": "https://i.redd.it/example.png?width=100&amp;format=png", "x": 100, "y": 80}}
        },
    }

    images, video, poster = reddit_media(post)

    assert images == [{"url": "https://i.redd.it/example.png?width=100&format=png", "width": 100, "height": 80}]
    assert video is None
    assert poster is None


def test_get_reddit_post_uses_configured_gallery_dl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A cache miss obtains post metadata from gallery-dl and saves it to disk."""
    post = {"id": "abc123", "title": "A post"}

    class DataJob:
        """Minimal gallery-dl job double."""

        def __init__(self, url: str, *, file: None) -> None:
            self.url = url
            self.file = file
            self.exception: Exception | None = None
            self.data_post = [post]

        def run(self) -> int:
            return 0

    monkeypatch.setattr("e.reddit.job.DataJob", DataJob)
    monkeypatch.setattr("e.reddit.data_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "set", lambda *_, **__: None)

    assert get_reddit_post("https://www.reddit.com/r/python/comments/abc123", "python", "abc123") == post
    cache_file = tmp_path / "Reddit" / "Downloads" / "python" / "abc123.json"
    assert json.loads(cache_file.read_text(encoding="utf-8")) == post


def test_get_reddit_post_uses_cached_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A cache hit avoids creating a gallery-dl job."""
    cache_file = tmp_path / "Reddit" / "Downloads" / "python" / "abc123.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text('{"id": "abc123", "title": "Cached"}', encoding="utf-8")
    monkeypatch.setattr("e.reddit.data_dir", lambda: tmp_path)
    monkeypatch.setattr("e.reddit.job.DataJob", lambda *_args, **_kwargs: None)

    post = get_reddit_post("https://www.reddit.com/r/python/comments/abc123", "python", "abc123")

    assert post == {"id": "abc123", "title": "Cached"}


def test_reddit_media_uses_hosted_progressive_video() -> None:
    """Videos use Reddit's hosted progressive MP4 URL."""
    post: dict[str, Any] = {
        "secure_media": {
            "reddit_video": {"fallback_url": "https://v.redd.it/example/DASH_720.mp4", "width": 720, "height": 1280}
        },
        "preview": {"images": [{"source": {"url": "https://preview.redd.it/example.jpg"}}]},
    }

    images, video, poster = reddit_media(post)

    assert images == []
    assert video is not None
    assert video["url"] == "https://v.redd.it/example/DASH_720.mp4"
    assert poster == "https://preview.redd.it/example.jpg"


def test_download_reddit_video_returns_cached_file_without_downloading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cached video file is reused without invoking gallery-dl."""
    monkeypatch.setattr("e.reddit.data_dir", lambda: tmp_path)
    cached_file = tmp_path / "Reddit" / "Media" / "python" / "abc123.mp4"
    cached_file.parent.mkdir(parents=True)
    cached_file.write_bytes(b"cached video")

    class DownloadJob:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            msg = "gallery-dl should not run when the file is already cached"
            raise AssertionError(msg)

    monkeypatch.setattr("e.reddit.job.DownloadJob", DownloadJob)

    result = download_reddit_video("https://www.reddit.com/r/python/comments/abc123", "python", "abc123")

    assert result == cached_file


def test_download_reddit_video_downloads_and_muxes_with_gallery_dl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A cache miss downloads and muxes the video via gallery-dl's DownloadJob."""
    monkeypatch.setattr("e.reddit.data_dir", lambda: tmp_path)
    output_path = reddit_media_path("python", "abc123")

    class DownloadJob:
        def __init__(self, url: str) -> None:
            assert url == "https://www.reddit.com/r/python/comments/abc123"

        def run(self) -> int:
            output_path.write_bytes(b"muxed video")
            return 0

    monkeypatch.setattr("e.reddit.job.DownloadJob", DownloadJob)

    result = download_reddit_video("https://www.reddit.com/r/python/comments/abc123", "python", "abc123")

    assert result == output_path
    assert result.read_bytes() == b"muxed video"


def test_download_reddit_video_raises_when_gallery_dl_reports_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A non-zero DownloadJob status is treated as a failed download."""
    monkeypatch.setattr("e.reddit.data_dir", lambda: tmp_path)

    class DownloadJob:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run(self) -> int:
            return 1

    monkeypatch.setattr("e.reddit.job.DownloadJob", DownloadJob)

    try:
        download_reddit_video("https://www.reddit.com/r/python/comments/abc123", "python", "abc123")
    except RuntimeError:
        pass
    else:
        pytest.fail("Expected a RuntimeError when gallery-dl reports a failed download")


def test_reddit_video_file_serves_cached_video(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The video route serves a previously downloaded, muxed file."""
    monkeypatch.setattr("e.reddit.data_dir", lambda: tmp_path)
    video_path = tmp_path / "Reddit" / "Media" / "python" / "abc123.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"muxed video")

    with TestClient(app=app) as client:
        response = client.get("/reddit/media/python/abc123/video.mp4")

    assert response.status_code == 200
    assert response.content == b"muxed video"


def test_reddit_video_file_missing_returns_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The video route 404s when the file hasn't been downloaded yet."""
    monkeypatch.setattr("e.reddit.data_dir", lambda: tmp_path)

    with TestClient(app=app) as client:
        response = client.get("/reddit/media/python/missing/video.mp4")

    assert response.status_code == 404


def test_non_discord_ip_redirects_to_reddit() -> None:
    """Ordinary browser requests preserve Reddit as the destination."""
    with TestClient(app=app) as client:
        response = client.get(
            "/r/python/comments/abc123/a-post",
            headers={"x-forwarded-for": "8.8.8.8"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "https://www.reddit.com/r/python/comments/abc123"


def test_reddit_embed_omits_twitter_alternate_links(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reddit embeds use generic Open Graph metadata without Twitter links."""
    monkeypatch.setattr(
        "e.reddit.get_reddit_post",
        lambda *_: {
            "title": "A post",
            "author": "someone",
            "ups": 10928,
            "num_comments": 165,
            "secure_media": {"reddit_video": {"fallback_url": "https://v.redd.it/example/CMAF_720.mp4"}},
            "preview": {"images": [{"source": {"url": "https://external-preview.redd.it/example.jpg"}}]},
        },
    )

    with TestClient(app=app) as client:
        response = client.get(
            "/r/python/comments/abc123/a-post",
            headers={"x-forwarded-for": "127.0.0.1"},
        )

    assert response.status_code == 200
    assert "application/activity+json" not in response.text
    assert "application/json+oembed" not in response.text
    assert '<meta property="og:title" content="A post">' in response.text
    assert "/u/someone in /r/python • 10.9K upvotes • 165 comments" in response.text
    assert '[/u/someone](https://www.reddit.com/user/someone)' in response.text
    assert '[/r/python](https://www.reddit.com/r/python)' in response.text
    assert 'content="https://v.redd.it/example/CMAF_720.mp4"' in response.text
    assert 'content="https://external-preview.redd.it/example.jpg"' in response.text


@pytest.mark.parametrize("path", ["/r/genshinsmols", "/r/genshinsmols/"])
def test_subreddit_route_redirects_browsers_to_reddit(path: str) -> None:
    """Subreddit URLs no longer 404 for ordinary clients."""
    with TestClient(app=app) as client:
        response = client.get(path, headers={"x-forwarded-for": "8.8.8.8"}, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://www.reddit.com/r/genshinsmols"


def test_subreddit_embed_shows_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """Discord gets an embed with member counts and the subreddit description."""
    about: dict[str, Any] = {
        "display_name": "genshinsmols",
        "display_name_prefixed": "r/genshinsmols",
        "title": "Genshin Smols",
        "public_description": "Cute Genshin art",
        "subscribers": 12_345,
        "active_user_count": 678,
        "created_utc": 1_600_000_000,
        "icon_img": "https://styles.redditmedia.com/icon.png?x=1&amp;y=2",
        "banner_img": "https://b.thumbs.redditmedia.com/banner.jpg",
    }
    monkeypatch.setattr("e.reddit.get_subreddit_about", lambda *_: about)

    with TestClient(app=app) as client:
        response = client.get("/r/genshinsmols/", headers={"x-forwarded-for": "127.0.0.1"})

    assert response.status_code == 200
    assert '<meta property="og:type" content="website">' in response.text
    assert '<meta property="og:title" content="/r/genshinsmols">' in response.text
    assert '# **[/r/genshinsmols](https://www.reddit.com/r/genshinsmols)**' in response.text
    assert '"type":11,"media"' in response.text
    assert "12.3K members" in response.text
    assert "678 online" in response.text
    assert "Created 2020-09-13" in response.text
    assert "Cute Genshin art" in response.text
    assert "r/genshinsmols |" not in response.text
    assert "https://b.thumbs.redditmedia.com/banner.jpg" in response.text
    assert "https://styles.redditmedia.com/icon.png?x=1\\u0026y=2" in response.text


def test_subreddit_embed_redirects_when_stats_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing OAuth credentials or API errors fall back to Reddit."""

    def fail(*_args: object) -> None:
        msg = "REDDIT_REFRESH_TOKEN is not configured"
        raise RuntimeError(msg)

    monkeypatch.setattr("e.reddit.get_subreddit_about", fail)

    with TestClient(app=app) as client:
        response = client.get(
            "/r/genshinsmols",
            headers={"x-forwarded-for": "127.0.0.1"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "https://www.reddit.com/r/genshinsmols"


def test_get_subreddit_about_uses_oauth_and_caches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Subreddit stats are fetched via OAuth once and then read from cache."""
    about: dict[str, Any] = {"display_name": "python", "subscribers": 42}
    calls: dict[str, tuple[str, dict[str, Any]]] = {}

    class TokenResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"access_token": "token"}

    class AboutResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"data": about}

    def fake_post(url: str, **kwargs: Any) -> TokenResponse:
        calls["post"] = (url, kwargs)
        return TokenResponse()

    def fake_get(url: str, **kwargs: Any) -> AboutResponse:
        calls["get"] = (url, kwargs)
        return AboutResponse()

    monkeypatch.setattr("e.reddit.data_dir", lambda: tmp_path)
    monkeypatch.setenv("REDDIT_REFRESH_TOKEN", "refresh")
    monkeypatch.setattr("e.reddit.niquests.post", fake_post)
    monkeypatch.setattr("e.reddit.niquests.get", fake_get)

    assert get_subreddit_about("python") == about
    assert calls["post"][1]["data"]["refresh_token"] == "refresh"
    assert calls["get"][0] == "https://oauth.reddit.com/r/python/about.json"
    assert calls["get"][1]["headers"]["Authorization"] == "bearer token"

    cache_file = tmp_path / "Reddit" / "Subreddits" / "python.json"
    assert json.loads(cache_file.read_text(encoding="utf-8")) == about

    calls.clear()
    assert get_subreddit_about("python") == about
    assert calls == {}


def test_get_subreddit_about_refreshes_expired_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Stale subreddit stats are re-fetched instead of being shown forever."""
    cache_file = tmp_path / "Reddit" / "Subreddits" / "python.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text('{"subscribers": 1}', encoding="utf-8")
    cache_mtime: float = cache_file.stat().st_mtime
    monkeypatch.setattr("e.reddit.time.time", lambda: cache_mtime + 400)

    class TokenResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, str]:
            return {"access_token": "token"}

    class AboutResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {"data": {"subscribers": 2}}

    monkeypatch.setattr("e.reddit.data_dir", lambda: tmp_path)
    monkeypatch.setenv("REDDIT_REFRESH_TOKEN", "refresh")
    monkeypatch.setattr("e.reddit.niquests.post", lambda *_args, **_kwargs: TokenResponse())
    monkeypatch.setattr("e.reddit.niquests.get", lambda *_args, **_kwargs: AboutResponse())

    assert get_subreddit_about("python") == {"subscribers": 2}
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {"subscribers": 2}


def test_get_subreddit_about_rejects_invalid_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Invalid subreddit names never reach the Reddit API or cache path."""
    monkeypatch.setattr("e.reddit.data_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match="Invalid subreddit"):
        get_subreddit_about("../etc")
