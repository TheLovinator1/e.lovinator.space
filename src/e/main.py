from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from gallery_dl import output
from litestar import Litestar
from litestar import get
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.response import Response
from litestar.template import TemplateConfig

from e.twitter import tweet_oembed
from e.twitter import tweet_status_api
from e.twitter import twitter
from e.twitter import users_statuses

output.initialize_logging(logging.INFO)

_STATIC_DIR = Path(__file__).parent / "static"
"""Directory containing the site icons."""

_icon_cache: dict[str, bytes] = {}


def _read_icon(name: str) -> bytes:
    """Read a static icon file, caching it in memory.

    Args:
        name: Filename inside the static directory.

    Returns:
        The file contents.
    """
    if name not in _icon_cache:
        _icon_cache[name] = (_STATIC_DIR / name).read_bytes()
    return _icon_cache[name]


@get("/favicon.ico")
async def favicon() -> Response:  # ruff: ignore[unused-async]
    """Return the site favicon."""
    return Response(
        content=_read_icon("favicon.ico"),
        media_type="image/x-icon",
    )


@get("/apple-touch-icon.png")
async def apple_touch_icon() -> Response:  # ruff: ignore[unused-async]
    """Return the site icon used in embed footers by Discord."""
    return Response(
        content=_read_icon("apple-touch-icon.png"),
        media_type="image/png",
    )


app = Litestar(
    route_handlers=[
        apple_touch_icon,
        favicon,
        tweet_oembed,
        tweet_status_api,
        twitter,
        users_statuses,
    ],
    debug=True,
    template_config=TemplateConfig(
        directory=Path(__file__).parent / "templates",
        engine=JinjaTemplateEngine,
    ),
)


def main() -> None:
    """Run the application with uvicorn."""
    uvicorn.run(
        "e.main:app",
        host="0.0.0.0",  # ruff: ignore[hardcoded-bind-all-interfaces]
        port=int(os.getenv("PORT", "8000")),
    )


if __name__ == "__main__":
    main()
