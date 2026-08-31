from __future__ import annotations

import asyncio
from ipaddress import ip_address
from typing import TYPE_CHECKING
from typing import Any

from litestar.testing import RequestFactory
from litestar.testing import TestClient

from e.main import app
from e.twitter import convert_urls_to_links
from e.twitter import get_client_ip
from e.twitter import is_discord_client

if TYPE_CHECKING:
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
