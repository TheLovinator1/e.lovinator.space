import logging

import uvicorn
from gallery_dl import output
from litestar import Litestar
from litestar import get
from litestar.response import Response

from e.twitter import twitter

output.initialize_logging(logging.INFO)


@get("/favicon.ico")
async def favicon() -> Response:  # ruff: ignore[unused-async]
    """Return empty response."""
    return Response(
        content=b"",
        media_type="image/x-icon",
        status_code=204,
    )


app = Litestar(route_handlers=[twitter, favicon], debug=True)

if __name__ == "__main__":
    import os

    uvicorn.run("e.main:app", port=int(os.getenv("PORT", str(8000))), reload=True)
