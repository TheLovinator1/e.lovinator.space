from __future__ import annotations

from typing import Any

from e.twitter import convert_urls_to_links


def make_facet(
    replacement: str,
    display: str | None = None,
    facet_type: str = "url",
) -> dict[str, Any]:
    facet: dict[str, Any] = {"type": facet_type, "replacement": replacement}
    if display is not None:
        facet["display"] = display
    return facet


def test_no_facets_returns_text_unchanged() -> None:
    text = "Hello world, no links here."
    assert convert_urls_to_links(text, []) == text


def test_single_url_is_wrapped_in_anchor() -> None:
    text = "Check this out: https://example.com/path"
    facets: list[dict[str, Any]] = [make_facet("https://example.com/path", display="example.com/path")]

    result: str = convert_urls_to_links(text, facets)

    assert '<a href="https://example.com/path">example.com/path</a>' in result
    assert result.count("<a ") == 1


def test_multiple_urls_are_all_wrapped() -> None:
    text = "First https://a.example/one then https://b.example/two"
    facets: list[dict[str, Any]] = [
        make_facet("https://a.example/one", display="a.example/one"),
        make_facet("https://b.example/two", display="b.example/two"),
    ]

    result: str = convert_urls_to_links(text, facets)

    assert '<a href="https://a.example/one">a.example/one</a>' in result
    assert '<a href="https://b.example/two">b.example/two</a>' in result
    assert result.count("<a ") == 2


def test_repeated_url_substring_is_wrapped_every_occurrence() -> None:
    text = "https://example.com/x appears twice: https://example.com/x"
    facets: list[dict[str, Any]] = [make_facet("https://example.com/x", display="example.com/x")]

    result: str = convert_urls_to_links(text, facets)

    assert result.count('<a href="https://example.com/x">example.com/x</a>') == 2


def test_facet_ordering_does_not_double_wrap_when_urls_overlap() -> None:
    # A facet whose replacement is a substring of another must not corrupt the longer anchor.
    text = "Long: https://example.com/path/extra Short: https://example.com/path"
    facets: list[dict[str, Any]] = [
        make_facet("https://example.com/path", display="short"),
        make_facet("https://example.com/path/extra", display="long"),
    ]

    result: str = convert_urls_to_links(text, facets)

    assert '<a href="https://example.com/path/extra">long</a>' in result
    assert '<a href="https://example.com/path">short</a>' in result


def test_missing_facets_list_returns_text_unchanged() -> None:
    text = "https://example.com/never-linked because there are no facets"
    assert convert_urls_to_links(text, []) == text


def test_empty_replacement_is_skipped() -> None:
    text = "Nothing should change here https://example.com"
    facets: list[dict[str, Any]] = [make_facet("", display="ignored")]

    assert convert_urls_to_links(text, facets) == text


def test_non_url_facet_type_is_ignored() -> None:
    text = "A mention @someone and a link https://example.com"
    facets: list[dict[str, Any]] = [make_facet("https://example.com", display="example.com", facet_type="mention")]

    assert convert_urls_to_links(text, facets) == text


def test_display_defaults_to_replacement_when_missing() -> None:
    text = "Visit https://example.com/page today"
    facets: list[dict[str, Any]] = [make_facet("https://example.com/page")]

    result: str = convert_urls_to_links(text, facets)

    assert '<a href="https://example.com/page">https://example.com/page</a>' in result


def test_html_special_characters_in_url_are_escaped() -> None:
    text = 'Click https://example.com/?a=1&b=2"x'
    facets: list[dict[str, Any]] = [
        make_facet('https://example.com/?a=1&b=2"x', display='https://example.com/?a=1&b=2"x')
    ]

    result: str = convert_urls_to_links(text, facets)

    assert "&amp;" in result
    assert "&quot;" in result
    assert '<a href="https://example.com/?a=1&amp;b=2&quot;x">' in result
    assert 'href="https://example.com/?a=1&b=2"x"' not in result


def test_html_special_characters_in_display_are_escaped() -> None:
    text = "Click https://example.com/danger"
    facets: list[dict[str, Any]] = [make_facet("https://example.com/danger", display='<script>alert("x")</script>')]

    result: str = convert_urls_to_links(text, facets)

    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_text_with_angle_brackets_and_ampersands_outside_urls_is_escaped() -> None:
    text = "1 < 2 & 3 > 1, see https://example.com for more"
    facets: list[dict[str, Any]] = [make_facet("https://example.com", display="example.com")]

    result: str = convert_urls_to_links(text, facets)

    # Surrounding text is HTML-escaped so tweet content can't inject markup; only the URL is linked.
    assert "1 &lt; 2 &amp; 3 &gt; 1, see" in result
    assert '<a href="https://example.com">example.com</a>' in result


def test_text_without_facets_is_still_escaped() -> None:
    text = "5 < 10 & 10 > 5"
    assert convert_urls_to_links(text, []) == "5 &lt; 10 &amp; 10 &gt; 5"


def test_javascript_scheme_is_not_linkified() -> None:
    text = "Click here for a surprise"
    facets: list[dict[str, Any]] = [make_facet("javascript:alert(1)", display="Click here")]

    result: str = convert_urls_to_links(text, facets)

    assert "<a " not in result
    assert result == text


def test_data_scheme_is_not_linkified() -> None:
    text = "Open this"
    facets: list[dict[str, Any]] = [make_facet("data:text/html,<script>alert(1)</script>", display="Open this")]

    result: str = convert_urls_to_links(text, facets)

    assert "<a " not in result
    assert "<script>" not in result
