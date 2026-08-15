"""Translate embed text into English with OpenAI."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from typing import TYPE_CHECKING

from anyio import to_thread
from loguru import logger
from openai import OpenAI

from e.settings import OPENAI_API_KEY
from e.settings import OPENAI_TRANSLATION_MODEL
from e.settings import TRANSLATIONS_PATH

if TYPE_CHECKING:
    from pathlib import Path

    from e.twitter import Embed

SYSTEM_PROMPT = (
    "You are a professional translator. Translate the text below into English. "
    "Preserve the tone of the original, and do not translate @mentions, "
    "hashtags, URLs, or proper nouns. Return only the translation, "
    "with no explanations or quotes."
)
"""System prompt for the translation model."""


class _Translator:
    """Translator with a lazy OpenAI client and an on-disk cache."""

    def __init__(self, *, cache_path: Path = TRANSLATIONS_PATH) -> None:
        """Initialize the translator.

        Args:
            cache_path: File the translation cache is persisted to.
        """
        self._cache_path = cache_path
        self._client: OpenAI | None = None
        self._cache: dict[str, str] = {}
        self._cache_loaded = False
        self._warned_missing_key = False
        self._lock = threading.Lock()

    def _load_cache(self) -> dict[str, str]:
        """Load the on-disk cache into memory.

        Callers must hold :attr:`_lock`.

        Returns:
            The in-memory cache.
        """
        if not self._cache_loaded:
            try:
                self._cache.update(json.loads(self._cache_path.read_text(encoding="utf-8")))
            except FileNotFoundError:
                pass
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Could not read translation cache {}: {}", self._cache_path, exc)
            self._cache_loaded = True
        return self._cache

    def _save_cache(self) -> None:
        """Write the cache to disk atomically."""
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            partial = self._cache_path.with_name(self._cache_path.name + ".part")
            partial.write_text(json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8")
            partial.replace(self._cache_path)
        except OSError as exc:
            logger.warning("Could not write translation cache {}: {}", self._cache_path, exc)

    def translate_text(self, text: str) -> str:
        """Translate a text into English, falling back to the original.

        Args:
            text: The text to translate.

        Returns:
            The English translation, or the original text when it is empty,
            no API key is configured, or the API call fails.
        """
        text = text.strip()
        if not text:
            return text

        with self._lock:
            cached = self._load_cache().get(text)
        if cached is not None:
            return cached

        client = self._client
        if client is None and OPENAI_API_KEY:
            client = OpenAI(api_key=OPENAI_API_KEY, base_url="https://api.deepseek.com")
            self._client = client
        if client is None:
            if not self._warned_missing_key:
                logger.warning("OPENAI_API_KEY is not set; /en routes will serve untranslated text")
                self._warned_missing_key = True
            return text

        try:
            completion = client.chat.completions.create(
                model=OPENAI_TRANSLATION_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                temperature=0,
            )
        except Exception as exc:  # ruff: ignore[blind-except] - translation must never break embeds
            logger.warning("OpenAI translation failed: {}", exc)
            return text

        translated = (completion.choices[0].message.content or "").strip()
        if not translated:
            return text

        with self._lock:
            self._cache[text] = translated
            self._save_cache()
        return translated


_translator = _Translator()


def translate_text(text: str) -> str:
    """Translate a text into English, using the module-wide cache.

    Args:
        text: The text to translate.

    Returns:
        The English translation, or the original text when unavailable.
    """
    return _translator.translate_text(text)


async def translate_embed(embed: Embed, fields: tuple[str, ...]) -> Embed:
    """Translate the given text fields of an embed into English.

    Fields whose text is empty or already in English are left unchanged.
    Translation runs in a worker thread so the event loop stays responsive.

    Args:
        embed: The embed to translate.
        fields: Names of the text fields to translate.

    Returns:
        A copy of the embed with the translated fields, or the original embed
        when nothing changed.
    """
    updates: dict[str, str] = {}
    for field in fields:
        value = getattr(embed, field)
        if value.strip():
            translated = await to_thread.run_sync(translate_text, value)
            if translated != value:
                updates[field] = translated
    return replace(embed, **updates) if updates else embed
