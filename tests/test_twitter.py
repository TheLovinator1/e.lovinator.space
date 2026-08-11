from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from selectolax.parser import HTMLParser

from e.twitter import generate_html
from e.twitter import parse_number
from e.twitter import parse_stats
from e.twitter import parse_tweet

if TYPE_CHECKING:
    from htpy._types import Renderable


NITTER_HTML_PATH = Path(__file__).parent / "nitter.htm"


def test_parse_number() -> None:
    """Test parse_number with various formats."""
    assert parse_number("46,391") == 46391
    assert parse_number("1,600") == 1600
    assert parse_number("1.5K") == 1500
    assert parse_number("2.5M") == 2500000
    assert parse_number("") is None


def test_parse_tweet_nitter_html() -> None:
    """Test parse_tweet using the nitter.htm sample file."""
    html_content = NITTER_HTML_PATH.read_text(encoding="utf-8")
    tweet = parse_tweet(html_content)

    assert tweet["author"]["name"] == "DiscussingFilm"
    assert tweet["author"]["username"] == "@DiscussingFilm"
    assert tweet["author"]["profile_url"] == "/DiscussingFilm"
    assert tweet["author"]["avatar"] == "/pic/profile_images%2F1706429397467549696%2FhmvwfChQ_bigger.jpg"
    assert tweet["author"]["verified"] is True

    assert tweet["date"]["relative"] == "Aug 8"
    assert tweet["date"]["published"] == "Aug 8, 2026 · 5:32 PM UTC"
    assert tweet["date"]["title"] == "Aug 8, 2026 · 5:32 PM UTC"
    assert tweet["date"]["url"] == "/DiscussingFilm/status/2086143411984208230#m"

    assert tweet["text"] is not None
    assert "Ryan Hurst has shared a photo" in tweet["text"]

    assert len(tweet["media"]) == 1
    assert tweet["media"][0]["url"] == "/pic/orig/media%2FHPN4YF0X0AAx-ug.jpg"
    assert tweet["media"][0]["thumbnail"] == "/pic/media%2FHPN4YF0X0AAx-ug.jpg%3Fname%3Dsmall%26format%3Dwebp"

    assert tweet["stats"] == {
        "comments": 555,
        "retweets": 1600,
        "likes": 50027,
        "views": 2224390,
    }


def test_generate_html_with_parsed_tweet() -> None:
    """Test generating HTML representation from parsed tweet."""
    html_content = NITTER_HTML_PATH.read_text(encoding="utf-8")
    tweet = parse_tweet(html_content)

    rendered: Renderable = generate_html(tweet)
    html_str = str(rendered)

    assert 'property="theme-color"' in html_str
    assert 'property="og:image"' in html_str
    assert tweet["media"][0]["thumbnail"] in html_str


def test_parse_tweet_invalid_html() -> None:
    """Test that parse_tweet raises ValueError when .tweet-body is missing."""
    with pytest.raises(ValueError, match=r"Could not find \.tweet-body"):
        parse_tweet("<html><body><div>No tweet here</div></body></html>")


def test_parse_stats_no_icon() -> None:
    """Test parse_stats behavior when stat elements are missing icon or classes."""
    html = '<div class="tweet-body"><div class="tweet-stat">No icon here</div></div>'
    tree = HTMLParser(html)
    tweet_node = tree.css_first(".tweet-body")
    assert tweet_node is not None
    stats = parse_stats(tweet_node)
    assert stats == {}
