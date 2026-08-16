from __future__ import annotations

from e.activity import DEFAULT_AUTHOR_TEXT
from e.activity import engagement_text
from e.activity import oembed_payload
from e.activity import status_payload


def test_engagement_text_omits_zero_and_missing() -> None:
    """Test that only nonzero counts are included."""
    assert not engagement_text()
    assert not engagement_text(comments=0, retweets=0, likes=0)
    assert engagement_text(comments=5, retweets=14, likes=140) == "💬 5   🔁 14   ❤️ 140"
    assert engagement_text(comments=25301) == "💬 25.3K"
    assert engagement_text(retweets=140) == "🔁 140"
    assert engagement_text(likes=1249683) == "❤️ 1.2M"


def test_engagement_text_separator() -> None:
    """Test that the separator is customizable for HTML content."""
    assert engagement_text(comments=5, retweets=14, likes=140, separator="&ensp;") == "💬 5&ensp;🔁 14&ensp;❤️ 140"


def test_status_payload_shape() -> None:
    """Test the Mastodon Status document shape."""
    payload = status_payload(
        status_id="123",
        url="https://twitter.com/u/status/123",
        created_at="2025-04-07T17:22:56.000Z",
        content="<p>hi</p>",
        account={"username": "u"},
        media=[{"type": "video", "url": "https://example.com/v.mp4"}],
        application={"name": "Twitter", "website": None},
    )

    assert payload["id"] == "123"
    assert payload["url"] == "https://twitter.com/u/status/123"
    assert payload["uri"] == "https://twitter.com/u/status/123"
    assert payload["created_at"] == "2025-04-07T17:22:56.000Z"
    assert payload["content"] == "<p>hi</p>"
    assert payload["account"] == {"username": "u"}
    assert payload["application"] == {"name": "Twitter", "website": None}
    assert payload["media_attachments"] == [{"type": "video", "url": "https://example.com/v.mp4"}]
    assert payload["reblog"] is None
    # The engagement counts are never emitted as fields; they render through
    # content and the oEmbed author_name instead.
    assert "replies_count" not in payload
    assert "reblogs_count" not in payload
    assert "favourites_count" not in payload
    assert "sensitive" not in payload


def test_oembed_payload_shape() -> None:
    """Test the oEmbed document shape."""
    payload = oembed_payload(
        author_name="💬 5   🔁 14   ❤️ 140",
        author_url="https://twitter.com/u/status/123",
        provider_url="https://e.lovinator.space",
    )

    assert payload["type"] == "rich"
    assert payload["version"] == "1.0"
    assert payload["author_name"] == "💬 5   🔁 14   ❤️ 140"
    assert payload["author_url"] == "https://twitter.com/u/status/123"
    assert payload["provider_name"] == "e.lovinator.space"
    assert payload["provider_url"] == "https://e.lovinator.space"
    assert payload["title"] == DEFAULT_AUTHOR_TEXT
