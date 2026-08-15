from __future__ import annotations

from typing import TYPE_CHECKING

from litestar.testing import TestClient

import e.media as media_module
from e.main import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

CONTENT = b"0123456789"


def _monkeypatch_roots(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(media_module, "MEDIA_ROOTS", (root,))


def test_media_serves_full_file(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that a media file is served with its content type."""
    _monkeypatch_roots(monkeypatch, tmp_dir)
    (tmp_dir / "1.mp4").write_bytes(CONTENT)

    with TestClient(app=app) as client:
        response = client.get("/media/1.mp4")

    assert response.status_code == 200
    assert response.content == CONTENT
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["accept-ranges"] == "bytes"


def test_media_serves_byte_range(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that a Range request returns 206 Partial Content."""
    _monkeypatch_roots(monkeypatch, tmp_dir)
    (tmp_dir / "1.mp4").write_bytes(CONTENT)

    with TestClient(app=app) as client:
        response = client.get("/media/1.mp4", headers={"Range": "bytes=2-5"})

    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"


def test_media_serves_open_ended_range(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that a Range without an end serves through the end of the file."""
    _monkeypatch_roots(monkeypatch, tmp_dir)
    (tmp_dir / "1.mp4").write_bytes(CONTENT)

    with TestClient(app=app) as client:
        response = client.get("/media/1.mp4", headers={"Range": "bytes=7-"})

    assert response.status_code == 206
    assert response.content == b"789"
    assert response.headers["content-range"] == "bytes 7-9/10"


def test_media_serves_suffix_range(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that a suffix Range serves the last N bytes."""
    _monkeypatch_roots(monkeypatch, tmp_dir)
    (tmp_dir / "1.mp4").write_bytes(CONTENT)

    with TestClient(app=app) as client:
        response = client.get("/media/1.mp4", headers={"Range": "bytes=-3"})

    assert response.status_code == 206
    assert response.content == b"789"
    assert response.headers["content-range"] == "bytes 7-9/10"


def test_media_range_not_satisfiable(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that an unsatisfiable Range returns 416."""
    _monkeypatch_roots(monkeypatch, tmp_dir)
    (tmp_dir / "1.mp4").write_bytes(CONTENT)

    with TestClient(app=app) as client:
        response = client.get("/media/1.mp4", headers={"Range": "bytes=99-"})

    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */10"


def test_media_serves_filename_with_spaces(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that a filename with spaces is resolved from the URL path."""
    _monkeypatch_roots(monkeypatch, tmp_dir)
    (tmp_dir / "my video.mp4").write_bytes(CONTENT)

    with TestClient(app=app) as client:
        response = client.get("/media/my%20video.mp4")

    assert response.status_code == 200
    assert response.content == CONTENT


def test_media_blocks_path_traversal(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that paths escaping the media root are not served."""
    _monkeypatch_roots(monkeypatch, tmp_dir)
    (tmp_dir / "1.mp4").write_bytes(CONTENT)

    with TestClient(app=app) as client:
        response = client.get("/media/../secret")

    assert response.status_code == 404
