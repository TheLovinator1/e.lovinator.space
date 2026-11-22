from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC
from datetime import datetime
from ipaddress import ip_address
from typing import TYPE_CHECKING
from typing import Any

import pytest
from litestar.testing import RequestFactory
from litestar.testing import TestClient

from e import twitter as twitter_module
from e.main import app
from e.twitter import build_discord_component_embed
from e.twitter import build_embed_footer
from e.twitter import contains_emoji
from e.twitter import convert_urls_to_links
from e.twitter import describe_video_quality
from e.twitter import get_client_ip
from e.twitter import is_discord_client
from e.twitter import select_video_rendition

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response
    from litestar import Litestar
    from litestar import Request


def make_facet(
    replacement: str,
    facet_type: str = "url",
) -> dict[str, Any]:
    return {"type": facet_type, "replacement": replacement}


def test_no_facets_returns_text_unchanged() -> None:
    text = "Hello world, no links here."
    assert convert_urls_to_links(text, []) == text


def test_single_url_is_wrapped_in_anchor() -> None:
    text = "Check this out: https://example.com/path"
    facets: list[dict[str, Any]] = [make_facet("https://example.com/path")]

    result: str = convert_urls_to_links(text, facets)

    assert '<a href="https://example.com/path">example.com/path</a>' in result
    assert result.count("<a ") == 1


def test_hashtag_is_wrapped_in_anchor() -> None:
    text = "Follow #Python for updates"
    facets: list[dict[str, Any]] = [{"type": "hashtag", "original": "Python"}]

    result: str = convert_urls_to_links(text, facets)

    assert '<a href="https://x.com/hashtag/Python">#Python</a>' in result


def test_hashtag_is_url_encoded() -> None:
    text = "A #C++ discussion"
    facets: list[dict[str, Any]] = [{"type": "hashtag", "original": "C++"}]

    result: str = convert_urls_to_links(text, facets)

    assert '<a href="https://x.com/hashtag/C%2B%2B">#C++</a>' in result


def test_multiple_urls_are_all_wrapped() -> None:
    text = "First https://a.example/one then https://b.example/two"
    facets: list[dict[str, Any]] = [
        make_facet("https://a.example/one"),
        make_facet("https://b.example/two"),
    ]

    result: str = convert_urls_to_links(text, facets)

    assert '<a href="https://a.example/one">a.example/one</a>' in result
    assert '<a href="https://b.example/two">b.example/two</a>' in result
    assert result.count("<a ") == 2


def test_repeated_url_substring_is_wrapped_every_occurrence() -> None:
    text = "https://example.com/x appears twice: https://example.com/x"
    facets: list[dict[str, Any]] = [make_facet("https://example.com/x")]

    result: str = convert_urls_to_links(text, facets)

    assert result.count('<a href="https://example.com/x">example.com/x</a>') == 2


def test_facet_ordering_does_not_double_wrap_when_urls_overlap() -> None:
    # A facet whose replacement is a substring of another must not corrupt the longer anchor.
    text = "Long: https://example.com/path/extra Short: https://example.com/path"
    facets: list[dict[str, Any]] = [
        make_facet("https://example.com/path"),
        make_facet("https://example.com/path/extra"),
    ]

    result: str = convert_urls_to_links(text, facets)

    assert '<a href="https://example.com/path/extra">example.com/path/extra</a>' in result
    assert '<a href="https://example.com/path">example.com/path</a>' in result


def test_missing_facets_list_returns_text_unchanged() -> None:
    text = "https://example.com/never-linked because there are no facets"
    assert convert_urls_to_links(text, []) == text


def test_empty_replacement_is_skipped() -> None:
    text = "Nothing should change here https://example.com"
    facets: list[dict[str, Any]] = [make_facet("")]

    assert convert_urls_to_links(text, facets) == text


def test_non_url_facet_type_is_ignored() -> None:
    text = "A mention @someone and a link https://example.com"
    facets: list[dict[str, Any]] = [make_facet("https://example.com", facet_type="mention")]

    assert convert_urls_to_links(text, facets) == text


def test_html_special_characters_in_url_are_escaped() -> None:
    text = 'Click https://example.com/?a=1&b=2"x'
    facets: list[dict[str, Any]] = [make_facet('https://example.com/?a=1&b=2"x')]

    result: str = convert_urls_to_links(text, facets)

    assert "&amp;" in result
    assert "&quot;" in result
    assert '<a href="https://example.com/?a=1&amp;b=2&quot;x">example.com/?a=1&amp;b=2&quot;x</a>' in result
    assert 'href="https://example.com/?a=1&b=2"x"' not in result


def test_text_with_angle_brackets_and_ampersands_outside_urls_is_escaped() -> None:
    text = "1 < 2 & 3 > 1, see https://example.com for more"
    facets: list[dict[str, Any]] = [make_facet("https://example.com")]

    result: str = convert_urls_to_links(text, facets)

    # Surrounding text is HTML-escaped so tweet content can't inject markup; only the URL is linked.
    assert "1 &lt; 2 &amp; 3 &gt; 1, see" in result
    assert '<a href="https://example.com">example.com</a>' in result


def test_text_without_facets_is_still_escaped() -> None:
    text = "5 < 10 & 10 > 5"
    assert convert_urls_to_links(text, []) == "5 &lt; 10 &amp; 10 &gt; 5"


def test_javascript_scheme_is_not_linkified() -> None:
    text = "Click here for a surprise"
    facets: list[dict[str, Any]] = [make_facet("javascript:alert(1)")]

    result: str = convert_urls_to_links(text, facets)

    assert "<a " not in result
    assert result == text


def test_data_scheme_is_not_linkified() -> None:
    text = "Open this"
    facets: list[dict[str, Any]] = [make_facet("data:text/html,<script>alert(1)</script>")]

    result: str = convert_urls_to_links(text, facets)

    assert "<a " not in result
    assert "<script>" not in result


def test_scheme_is_stripped_from_visible_label_but_kept_in_href() -> None:
    text = "See http://example.com/path and https://example.org/other"
    facets: list[dict[str, Any]] = [
        make_facet("http://example.com/path"),
        make_facet("https://example.org/other"),
    ]

    result: str = convert_urls_to_links(text, facets)

    assert '<a href="http://example.com/path">example.com/path</a>' in result
    assert '<a href="https://example.org/other">example.org/other</a>' in result


def make_video(**overrides: Any) -> dict[str, Any]:
    """Return an fxtwitter-style video object for the 4K GTA VI tweet."""
    video: dict[str, Any] = {
        "id": "1",
        "url": "https://video.twimg.com/amplify_video/1/vid/avc1/3840x2160/4k.mp4?tag=29",
        "thumbnail_url": "https://pbs.twimg.com/thumb.jpg",
        "duration": 363.366,
        "width": 3840,
        "height": 2160,
        "format": "video/mp4",
        "formats": [
            {"url": "https://video.twimg.com/amplify_video/1/pl/playlist.m3u8?tag=29", "container": "m3u8"},
            {
                "url": "https://video.twimg.com/amplify_video/1/vid/avc1/480x270/low.mp4?tag=29",
                "bitrate": 256000,
                "container": "mp4",
                "codec": "h264",
            },
            {
                "url": "https://video.twimg.com/amplify_video/1/vid/avc1/640x360/mid.mp4?tag=29",
                "bitrate": 832000,
                "container": "mp4",
                "codec": "h264",
            },
            {
                "url": "https://video.twimg.com/amplify_video/1/vid/avc1/1280x720/hd.mp4?tag=29",
                "bitrate": 2176000,
                "container": "mp4",
                "codec": "h264",
            },
            {
                "url": "https://video.twimg.com/amplify_video/1/vid/avc1/1920x1080/fhd.mp4?tag=29",
                "bitrate": 10368000,
                "container": "mp4",
                "codec": "h264",
            },
            {
                "url": "https://video.twimg.com/amplify_video/1/vid/avc1/3840x2160/4k.mp4?tag=29",
                "bitrate": 25128000,
                "container": "mp4",
                "codec": "h264",
            },
        ],
    }
    video.update(overrides)
    return video


def test_long_4k_video_uses_smaller_rendition() -> None:
    """A 6-minute 4K video (1.1 GB) should fall back to 720p so Discord can embed it."""
    url, width, height = select_video_rendition(make_video())

    assert url.endswith("/1280x720/hd.mp4?tag=29")
    assert (width, height) == (1280, 720)


def test_short_4k_video_is_capped_at_1080p() -> None:
    """4K is valid but wasteful; a short clip should still use 1080p, not 4K."""
    url, width, height = select_video_rendition(make_video(duration=5))

    assert url.endswith("/1920x1080/fhd.mp4?tag=29")
    assert (width, height) == (1920, 1080)


def test_video_without_formats_uses_top_level_url() -> None:
    """Older API responses only have the top-level URL."""
    video = {
        "url": "https://video.twimg.com/amplify_video/1/vid/avc1/1280x720/only.mp4?tag=29",
        "width": 1280,
        "height": 720,
        "duration": 10,
    }

    url, width, height = select_video_rendition(video)

    assert url == video["url"]
    assert (width, height) == (1280, 720)


def test_video_larger_than_budget_uses_smallest_rendition() -> None:
    """When every rendition is too big, pick the smallest instead of the 4K file."""
    url, width, height = select_video_rendition(make_video(duration=36_000))

    assert url.endswith("/480x270/low.mp4?tag=29")
    assert (width, height) == (480, 270)


def test_quality_note_compares_rendition_to_original() -> None:
    """The warning should state both resolutions and how much better the original is."""
    note = describe_video_quality(
        make_video(),
        "https://video.twimg.com/amplify_video/1/vid/avc1/1280x720/hd.mp4?tag=29",
        1280,
        720,
    )

    assert note.startswith("⚠️ Higher quality on X: 3840×2160")
    assert "1.1 GB" in note
    assert "9.0× the pixels" in note
    assert "11.5× the bitrate" in note


def test_quality_note_is_omitted_for_original_rendition() -> None:
    """No warning is needed when the embed already uses the source resolution."""
    video = make_video(width=1280, height=720)
    note = describe_video_quality(
        video,
        "https://video.twimg.com/amplify_video/1/vid/avc1/1280x720/hd.mp4?tag=29",
        1280,
        720,
    )

    assert note == ""


def test_component_embed_renders_separator_and_quality_warning() -> None:
    """The warning is added below the tweet, after a separator."""
    payload = build_discord_component_embed(
        author_name="Dee Batch",
        text="Played GTA VI",
        description="👁️ 1K",
        username="Dee_Batch",
        images=[],
        video=make_video(),
        quality_note="⚠️ Higher quality on X",
    )

    components = payload["component"]["components"]
    separator_index = next(index for index, component in enumerate(components) if component.get("type") == 14)
    warning_index = next(
        index
        for index, component in enumerate(components)
        if component.get("type") == 10 and "Higher quality on X" in component.get("content", "")
    )
    assert separator_index < warning_index


def test_component_embed_links_full_name_without_emoji() -> None:
    """A plain display name is clickable and points to the tweet."""
    tweet_url: str = "https://x.com/someuser/status/123"
    payload: dict[str, Any] = build_discord_component_embed(
        author_name="Some User",
        text="hello",
        description="",
        username="someuser",
        images=[],
        video=None,
        tweet_url=tweet_url,
    )

    content: str = payload["component"]["components"][0]["content"]
    assert content == f"## [Some User (@someuser)]({tweet_url})\n\nhello"


def test_component_embed_keeps_emoji_out_of_link_label() -> None:
    """Discord rejects emoji in masked links, so only the handle is linked."""
    tweet_url: str = "https://x.com/Elizabeth000425/status/123"
    payload: dict[str, Any] = build_discord_component_embed(
        author_name="Elizabeth.R🥀MERCREW",
        text="hello",
        description="",
        username="Elizabeth000425",
        images=[],
        video=None,
        tweet_url=tweet_url,
    )

    content: str = payload["component"]["components"][0]["content"]
    assert content == f"## Elizabeth.R🥀MERCREW [@Elizabeth000425]({tweet_url})\n\nhello"


def test_contains_emoji() -> None:
    """Emoji are detected in display names without flagging plain text."""
    assert contains_emoji("Elizabeth.R🥀MERCREW")
    assert not contains_emoji("Some User")


def test_embed_footer_uses_relative_discord_timestamp() -> None:
    """The tweet time becomes a Discord timestamp rendered in the viewer's locale."""
    created_timestamp: int = int(datetime(2026, 10, 22, 21, 54, tzinfo=UTC).timestamp())

    footer: str = build_embed_footer(
        {"based_in": "Japan", "source": "Japan App Store"},
        created_timestamp,
    )

    assert footer == f"Japan • Japan App Store • <t:{created_timestamp}:R>"


def test_embed_footer_without_timestamp_keeps_og_format() -> None:
    """The OG footer is unchanged: Discord appends the timestamp itself there."""
    assert build_embed_footer({"based_in": "Japan", "source": "Japan App Store"}) == "Japan • Japan App Store"


def test_embed_footer_falls_back_to_site_name_without_about_account() -> None:
    """Without location or source, the footer keeps the old site-name fallback."""
    assert build_embed_footer({}, 0) == "e.lovinator.space"


def test_component_embed_has_footer_as_last_text_display() -> None:
    """The footer is a text component at the bottom of the Component Embed."""
    footer: str = "Japan • Japan App Store • <t:1792706040:R>"
    payload: dict[str, Any] = build_discord_component_embed(
        author_name="Some User",
        text="hello",
        description="",
        username="someuser",
        images=[],
        video=None,
        footer=footer,
    )

    components: list[dict[str, Any]] = payload["component"]["components"]
    assert components[-1] == {"type": 10, "content": footer}


def test_tweet_page_renders_footer_in_og_and_component(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The OG footer stays unchanged while the Component Embed adds the tweet date."""
    monkeypatch.setattr(twitter_module, "data_dir", lambda: tmp_path)
    created_timestamp: int = int(datetime(2026, 10, 22, 21, 54, tzinfo=UTC).timestamp())
    cache_file: Path = twitter_module.tweet_cache_path("123")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps({
            "status": {"id": "123", "text": "hello", "created_timestamp": created_timestamp},
            "author": {
                "id": "1",
                "name": "Some User",
                "screen_name": "someuser",
                "about_account": {"based_in": "Japan", "source": "Japan App Store"},
            },
        }),
        encoding="utf-8",
    )
    client: TestClient[Litestar] = TestClient(app=app)

    response: Response = client.get("/someuser/status/123", headers={"x-forwarded-for": "127.0.0.1"})

    assert response.status_code == 200
    assert 'og:site_name" content="Japan • Japan App Store"' in response.text

    script: re.Match[str] | None = re.search(
        r'<script id="discord:component-embed" type="application/json">(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    assert script is not None
    payload: dict[str, Any] = json.loads(script.group(1))
    footer: str = f"Japan • Japan App Store • <t:{created_timestamp}:R>"
    components: list[dict[str, Any]] = payload["component"]["components"]
    assert components[0]["content"].startswith("## [Some User (@someuser)](https://x.com/someuser/status/123)")
    assert any(component.get("content") == footer for component in components)


def test_get_client_ip_headers() -> None:
    """Test extracting client IP from request headers."""
    req_xff: Request[Any, Any, Any] = RequestFactory().get("/", headers={"x-forwarded-for": "8.8.8.8, 1.1.1.1"})
    assert get_client_ip(req_xff) == ip_address("8.8.8.8")

    req_real: Request[Any, Any, Any] = RequestFactory().get("/", headers={"x-real-ip": "1.1.1.1"})
    assert get_client_ip(req_real) == ip_address("1.1.1.1")

    req_cf: Request[Any, Any, Any] = RequestFactory().get("/", headers={"cf-connecting-ip": "9.9.9.9"})
    assert get_client_ip(req_cf) == ip_address("9.9.9.9")

    req_invalid: Request[Any, Any, Any] = RequestFactory().get("/", headers={"x-forwarded-for": "invalid-ip"})
    assert get_client_ip(req_invalid) is None


def test_is_discord_client() -> None:
    """Test checking if an IP belongs to Discord or loopback."""
    assert asyncio.run(is_discord_client(ip_address("127.0.0.1"))) is True
    assert asyncio.run(is_discord_client(ip_address("::1"))) is True
    assert asyncio.run(is_discord_client(ip_address("104.196.222.45"))) is True
    assert asyncio.run(is_discord_client(ip_address("8.8.8.8"))) is False


def test_non_discord_ip_redirects_to_tweet() -> None:
    """Test non-Discord clients are redirected to the target tweet on x.com."""
    client: TestClient[Litestar] = TestClient(app=app)
    response: Response = client.get(
        "/someuser/status/123456789",
        headers={"x-forwarded-for": "8.8.8.8"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers.get("location") == "https://x.com/someuser/status/123456789"


def write_tweet_cache(tmp_path: Path, tweet_id: str, text: str) -> Path:
    """Write a minimal fxtwitter-style cache file for tests.

    Args:
        tmp_path: Temporary directory provided by pytest.
        tweet_id: The status ID to write cache data for.
        text: The tweet text to store.

    Returns:
        The path of the cache file that was written.
    """
    cache_file: Path = twitter_module.tweet_cache_path(tweet_id)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps({
            "status": {"id": tweet_id, "text": text},
            "author": {"id": "1", "name": "Some User", "screen_name": "someuser"},
        }),
        encoding="utf-8",
    )
    return cache_file


class FakeResponse:
    """Minimal stand-in for a niquests response."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Store the payload that ``json()`` will return."""
        self.payload = payload

    def raise_for_status(self) -> None:
        """Pretend the request succeeded."""

    def json(self) -> dict[str, Any]:
        """Return the fabricated API payload."""
        return self.payload


def test_save_tweet_cache_writes_formatted_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Saved JSON should be indented so served files are easy to read."""
    monkeypatch.setattr(twitter_module, "data_dir", lambda: tmp_path)
    payload: dict[str, Any] = {"status": {"id": "123", "text": "héllo"}}

    twitter_module.save_tweet_cache("123", payload)

    saved: str = twitter_module.tweet_cache_path("123").read_text(encoding="utf-8")
    assert saved == json.dumps(payload, ensure_ascii=False, indent=2)


def test_get_tweet_uses_cache_without_fetching(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A cached tweet should be read from disk without calling fxtwitter."""
    monkeypatch.setattr(twitter_module, "data_dir", lambda: tmp_path)
    write_tweet_cache(tmp_path, "123", "cached text")

    def unexpected_fetch(*args: object, **kwargs: object) -> None:
        msg = "fxtwitter should not be called when the cache exists"
        raise AssertionError(msg)

    monkeypatch.setattr(twitter_module.niquests, "get", unexpected_fetch)

    result = twitter_module.get_tweet("123")

    assert result["status"]["text"] == "cached text"


def test_get_tweet_translates_cached_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Translations must still be applied when the JSON comes from disk."""
    monkeypatch.setattr(twitter_module, "data_dir", lambda: tmp_path)
    cache_file: Path = write_tweet_cache(tmp_path, "123", "original")
    cached: dict[str, Any] = json.loads(cache_file.read_text(encoding="utf-8"))
    cached["status"]["translation"] = {"text": "translated"}
    cache_file.write_text(json.dumps(cached), encoding="utf-8")

    result = twitter_module.get_tweet("123")

    assert result["status"]["text"] == "translated"


def test_force_refresh_fetches_and_overwrites_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Force refresh should replace the cached JSON with a fresh API response."""
    monkeypatch.setattr(twitter_module, "data_dir", lambda: tmp_path)
    cache_file: Path = write_tweet_cache(tmp_path, "123", "old")
    fetched: dict[str, Any] = {"status": {"id": "123", "text": "new"}, "author": {"screen_name": "someuser"}}
    monkeypatch.setattr(twitter_module.niquests, "get", lambda *a, **kw: FakeResponse(fetched))

    result = twitter_module.get_tweet("123", force_refresh=True)

    assert result["status"]["text"] == "new"
    assert json.loads(cache_file.read_text(encoding="utf-8"))["status"]["text"] == "new"


@pytest.mark.parametrize("path", ["/twitter/json/someuser/123", "/someuser/status/123/json"])
def test_json_endpoint_serves_cached_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
) -> None:
    """The JSON endpoints should return the saved JSON inline for browser viewing."""
    monkeypatch.setattr(twitter_module, "data_dir", lambda: tmp_path)
    write_tweet_cache(tmp_path, "123", "hello")

    def unexpected_fetch(*args: object, **kwargs: object) -> None:
        msg = "fxtwitter should not be called when the cache exists"
        raise AssertionError(msg)

    monkeypatch.setattr(twitter_module.niquests, "get", unexpected_fetch)
    client: TestClient[Litestar] = TestClient(app=app)

    response: Response = client.get(path)

    assert response.status_code == 200
    assert response.json()["status"]["text"] == "hello"
    assert "inline" in response.headers["content-disposition"]
    assert "attachment" not in response.headers["content-disposition"]
    assert "123.json" in response.headers["content-disposition"]


@pytest.mark.parametrize("path", ["/twitter/json/someuser/123", "/someuser/status/123/json"])
def test_any_query_string_refreshes_json_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
) -> None:
    """Any query string should replace the cached JSON before serving it."""
    monkeypatch.setattr(twitter_module, "data_dir", lambda: tmp_path)
    cache_file: Path = write_tweet_cache(tmp_path, "123", "old")
    fetched: dict[str, Any] = {"status": {"id": "123", "text": "fresh"}, "author": {"screen_name": "someuser"}}
    monkeypatch.setattr(twitter_module.niquests, "get", lambda *a, **kw: FakeResponse(fetched))
    client: TestClient[Litestar] = TestClient(app=app)

    response: Response = client.get(f"{path}?whatever=1")

    assert response.status_code == 200
    assert response.json()["status"]["text"] == "fresh"
    assert json.loads(cache_file.read_text(encoding="utf-8"))["status"]["text"] == "fresh"


def test_component_embed_has_no_buttons() -> None:
    """The component embed has no action row; JSON is available at /json."""
    payload: dict[str, Any] = build_discord_component_embed(
        author_name="Some User",
        text="hello",
        description="",
        username="someuser",
        images=[],
        video=None,
    )

    components: list[dict[str, Any]] = payload["component"]["components"]
    assert components[0]["type"] == 10
    assert all(component.get("type") != 1 for component in components)


def test_tweet_page_links_to_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The embed page should also expose a regular link to the JSON file."""
    monkeypatch.setattr(twitter_module, "data_dir", lambda: tmp_path)
    write_tweet_cache(tmp_path, "123", "hello")
    client: TestClient[Litestar] = TestClient(app=app)

    response: Response = client.get("/someuser/status/123", headers={"x-forwarded-for": "127.0.0.1"})

    assert response.status_code == 200
    assert "/someuser/status/123/json" in response.text
    assert ">JSON</a>" in response.text


@pytest.mark.parametrize("query", ["fasfsafas", "v=2", "x", "t=123&foo=bar"])
def test_any_query_string_refreshes_embed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, query: str) -> None:
    """Appending any query string to the embed URL should refresh the tweet."""
    monkeypatch.setattr(twitter_module, "data_dir", lambda: tmp_path)
    cache_file: Path = write_tweet_cache(tmp_path, "123", "old")
    fetched: dict[str, Any] = {
        "status": {"id": "123", "text": "fresh"},
        "author": {"id": "1", "name": "Some User", "screen_name": "someuser"},
    }
    monkeypatch.setattr(twitter_module.niquests, "get", lambda *a, **kw: FakeResponse(fetched))
    client: TestClient[Litestar] = TestClient(app=app)

    response: Response = client.get(
        f"/someuser/status/123?{query}",
        headers={"x-forwarded-for": "127.0.0.1"},
    )

    assert response.status_code == 200
    assert "fresh" in response.text
    assert json.loads(cache_file.read_text(encoding="utf-8"))["status"]["text"] == "fresh"
