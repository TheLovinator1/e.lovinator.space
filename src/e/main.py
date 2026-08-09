import uvicorn
from litestar import Litestar
from litestar import get


@get("/")
async def twitter() -> dict[str, str]:  # ruff: ignore[unused-async]
    """Handle Twitter requests.

    https://twitter.com/DiscussingFilm/status/2086143411984208230
    https://e.lovinator.space/DiscussingFilm/status/2086143411984208230

    If IP is from Discord:
    Download the image/video.
    Return custom HTML with metadata tags with the image/video.

    Otherwise:
    Redirect to the original URL.

    Returns:
        Redirect to the original URL, or custom HTML with metadata tags.
    """
    return {"message": "Hello, World!"}


app = Litestar(route_handlers=[twitter])

if __name__ == "__main__":
    uvicorn.run(app)
