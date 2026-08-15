from __future__ import annotations

import logging
import os

import uvicorn
from gallery_dl import output
from litestar import Litestar
from litestar import get
from litestar.response import Response

from e.media import media
from e.reddit import reddit
from e.twitter import twitter

output.initialize_logging(logging.INFO)


@get("/favicon.ico")
async def favicon() -> Response:  # ruff: ignore[unused-async]
    """Return an empty favicon response."""
    return Response(
        content=b"",
        media_type="image/x-icon",
        status_code=204,
    )


app = Litestar(
    route_handlers=[
        twitter,
        reddit,
        media,
        favicon,
    ],
    debug=True,
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
