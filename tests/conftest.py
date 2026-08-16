"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from e import twitter as twitter_module

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def tmp_dir() -> Iterator[Path]:
    """Create a unique temporary directory inside the workspace.

    pytest's built-in ``tmp_path`` fixture uses ``tempfile.mkdtemp``, which
    creates directories with mode ``0o700``.  Under this sandbox the resulting
    directory is not writable afterwards, so we create one with a permissive
    mode instead.

    Yields:
        A writable temporary directory that is removed after the test.
    """
    path = Path.cwd() / f"tmp_e_{uuid.uuid4().hex}"
    path.mkdir(mode=0o777)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def clear_caches() -> None:
    """Clear module-level TTL caches before each test.

    The twitter module caches fetched metadata and resolved avatars in memory;
    without clearing them, tests that reuse the same URLs would read results
    from earlier tests' mocks.
    """
    twitter_module._meta_cache.clear()
    twitter_module._avatar_cache.clear()
