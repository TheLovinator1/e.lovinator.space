from __future__ import annotations

import re
from typing import TYPE_CHECKING
from typing import Annotated

from litestar import Request
from litestar import get
from litestar.exceptions import NotFoundException
from litestar.params import PathParameter
from litestar.response import Response
from litestar.response import Stream

from e.settings import MEDIA_ROUTE
from e.settings import REDDIT_MEDIA_DIR
from e.settings import TWITTER_MEDIA_DIR
from e.twitter import content_type_for

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK_SIZE = 64 * 1024

MEDIA_ROOTS: tuple[Path, ...] = (TWITTER_MEDIA_DIR, REDDIT_MEDIA_DIR)
"""Directories the media route serves files from."""


def resolve_media(relative_path: str) -> Path | None:
    """Resolve a URL path to a file inside one of the media roots.

    Args:
        relative_path: The decoded path from the request URL.

    Returns:
        The resolved file path, or ``None`` if no media root contains it.
    """
    # Litestar's ``path`` parameter includes the leading slash.
    relative_path = relative_path.lstrip("/")
    for root in MEDIA_ROOTS:
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _stream_range(path: Path, start: int, length: int) -> Iterator[bytes]:
    """Yield ``length`` bytes of ``path`` starting at ``start``.

    This is a synchronous generator; Litestar runs each ``next()`` call in a
    worker thread, so the blocking reads do not stall the event loop.
    """
    with path.open("rb") as file:
        file.seek(start)
        remaining = length
        while remaining > 0:
            chunk = file.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@get(f"{MEDIA_ROUTE}/{{file_path:path}}", sync_to_thread=True)
def media(
    request: Request,
    file_path: Annotated[str, PathParameter()],
) -> Response:
    """Serve a media file, honoring HTTP Range requests for video seeking.

    Args:
        request: The incoming request.
        file_path: The decoded path of the file, relative to a media root.

    Returns:
        A full or partial file response.

    Raises:
        NotFoundException: If the file is not inside a media root.
    """
    path = resolve_media(file_path)
    if path is None:
        raise NotFoundException(detail="File not found")

    size = path.stat().st_size
    content_type = content_type_for(path)

    start: int | None = None
    end: int | None = None
    if (range_header := request.headers.get("range")) and (match := _RANGE_RE.match(range_header.strip())):
        start_str, end_str = match.groups()
        if start_str or end_str:
            if not start_str:
                # Suffix range: the last N bytes.
                start = max(0, size - int(end_str))
                end = size - 1
            else:
                start = int(start_str)
                end = int(end_str) if end_str else size - 1

    if start is not None:
        end = min(end if end is not None else size - 1, size - 1)
        if start > end or start >= size:
            return Response(
                content=b"",
                status_code=416,
                headers={"Content-Range": f"bytes */{size}"},
            )
        length = end - start + 1
        return Stream(
            _stream_range(path, start, length),
            status_code=206,
            media_type=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(length),
            },
        )

    return Stream(
        _stream_range(path, 0, size),
        status_code=200,
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(size),
        },
    )
