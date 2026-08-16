from __future__ import annotations

from typing import Any

PROVIDER_NAME = "e.lovinator.space"
"""Name shown in the embed footer and oEmbed documents."""

DEFAULT_AUTHOR_TEXT = "Embed"
"""oEmbed author text used when there are no counts to show."""

_MILLION = 1_000_000
"""Number of units in a million."""

_THOUSAND = 1_000
"""Number of units in a thousand."""


def compact_number(value: int) -> str:
    """Format a count compactly, e.g. ``1234`` as ``1.2K``.

    Args:
        value: The count.

    Returns:
        The compact representation.
    """
    if value >= _MILLION:
        amount, suffix = value / _MILLION, "M"
    elif value >= _THOUSAND:
        amount, suffix = value / _THOUSAND, "K"
    else:
        return str(value)

    return f"{amount:.1f}".rstrip("0").rstrip(".") + suffix


def engagement_text(
    *,
    comments: int | None = None,
    retweets: int | None = None,
    likes: int | None = None,
) -> str:
    """Format engagement counts the way Discord renders them in the body.

    Args:
        comments: Number of replies.
        retweets: Number of reposts.
        likes: Number of favourites.

    Returns:
        e.g. ``💬 5   🔁 14   ❤️ 140``, with zero or missing metrics omitted.
    """
    parts: list[str] = []
    for value, emoji in ((comments, "💬"), (retweets, "🔁"), (likes, "❤️")):
        if isinstance(value, int) and value > 0:
            parts.append(f"{emoji} {compact_number(value)}")
    return "   ".join(parts)


def status_payload(  # ruff: ignore[too-many-arguments]
    *,
    status_id: str,
    url: str,
    created_at: str,
    content: str,
    account: dict[str, Any],
    media: list[dict[str, Any]],
    application: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Mastodon API ``Status`` document.

    Field names — and equally which fields are ABSENT — follow the wire format
    Discord's crawler accepts for Mastodon pages: notably the engagement counts
    are rendered through ``content`` and the oEmbed ``author_name``, and must
    never appear as ``replies_count``/``reblogs_count``/``favourites_count``.

    Args:
        status_id: The numeric status id.
        url: The canonical URL of the post.
        created_at: ISO-8601 timestamp of the post.
        content: HTML body, including any engagement counts.
        account: Mastodon ``Account`` document.
        media: List of ``MediaAttachment`` documents.
        application: The client application document.

    Returns:
        The Status document, shaped after
        https://docs.joinmastodon.org/entities/Status/
    """
    return {
        "id": str(status_id),
        "url": url,
        "uri": url,
        "created_at": created_at,
        "edited_at": None,
        "reblog": None,
        "in_reply_to_id": None,
        "in_reply_to_account_id": None,
        "language": None,
        "content": content,
        "spoiler_text": "",
        "visibility": "public",
        "application": application or {"name": "Twitter", "website": None},
        "media_attachments": media,
        "account": account,
        "mentions": [],
        "tags": [],
        "emojis": [],
        "card": None,
        "poll": None,
    }


def oembed_payload(
    *,
    author_name: str,
    author_url: str,
    provider_url: str,
) -> dict[str, Any]:
    """Build an oEmbed response document.

    Discord reads ``author_name`` for the small line above the embed title.

    Args:
        author_name: Text shown above the embed title.
        author_url: URL the author name links to.
        provider_url: URL the provider name links to.

    Returns:
        The oEmbed document.
    """
    return {
        "type": "rich",
        "version": "1.0",
        "author_name": author_name,
        "author_url": author_url,
        "provider_name": PROVIDER_NAME,
        "provider_url": provider_url,
        "title": DEFAULT_AUTHOR_TEXT,
    }
