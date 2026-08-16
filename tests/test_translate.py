from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import NoReturn

import e.translate as translate_module
from e.twitter import Embed

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

# The keyword arguments translate.py sends to chat completions.
_CreateKwargs = str | list[dict[str, str]] | int


def _fake_client(content: str) -> SimpleNamespace:
    """Build a fake DeepSeek client returning ``content`` from chat completions.

    Args:
        content: The translation the fake model should return.

    Returns:
        A fake client with a ``calls`` list recording each request.
    """
    calls: list[dict[str, _CreateKwargs]] = []

    def create(**kwargs: _CreateKwargs) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        calls=calls,
    )


def test_translate_text_returns_original_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_dir: Path,
) -> None:
    """Test that texts are returned unchanged when no API key is configured."""
    translator = translate_module._Translator(cache_path=tmp_dir / "translations.json")
    monkeypatch.setattr(translate_module, "DEEPSEEK_API_KEY", None)

    assert translator.translate_text("Hallå världen") == "Hallå världen"
    assert not (tmp_dir / "translations.json").exists()


def test_translate_text_caches_translations(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that translations are cached on disk and reused."""
    cache_path = tmp_dir / "translations.json"
    monkeypatch.setattr(translate_module, "DEEPSEEK_API_KEY", "test-key")

    translator = translate_module._Translator(cache_path=cache_path)
    client = _fake_client("Hello world")
    monkeypatch.setattr(translator, "_client", client)

    assert translator.translate_text("Hallå världen") == "Hello world"
    assert translator.translate_text("Hallå världen") == "Hello world"
    assert len(client.calls) == 1

    assert json.loads(cache_path.read_text(encoding="utf-8")) == {"Hallå världen": "Hello world"}

    # A fresh translator with the same cache file does not call the API again.
    fresh = translate_module._Translator(cache_path=cache_path)
    fresh_client = _fake_client("Never used")
    monkeypatch.setattr(fresh, "_client", fresh_client)

    assert fresh.translate_text("Hallå världen") == "Hello world"
    assert fresh_client.calls == []


def test_translate_text_handles_api_errors(monkeypatch: pytest.MonkeyPatch, tmp_dir: Path) -> None:
    """Test that API failures fall back to the original text."""
    translator = translate_module._Translator(cache_path=tmp_dir / "translations.json")
    monkeypatch.setattr(translate_module, "DEEPSEEK_API_KEY", "test-key")

    def create(**kwargs: _CreateKwargs) -> NoReturn:
        msg = "API is down"
        raise RuntimeError(msg)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(translator, "_client", client)

    assert translator.translate_text("Hallå världen") == "Hallå världen"


def test_translate_text_returns_original_when_model_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_dir: Path,
) -> None:
    """Test that empty model responses fall back to the original text."""
    translator = translate_module._Translator(cache_path=tmp_dir / "translations.json")
    monkeypatch.setattr(translate_module, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(translator, "_client", _fake_client(""))

    assert translator.translate_text("Hallå världen") == "Hallå världen"


def test_translate_embed_translates_given_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that translate_embed translates the requested fields in a copy."""
    embed = Embed(
        title="Rubrik",
        description="Brödtext",
        url="https://twitter.com/example/status/1",
        media=(),
    )

    def fake_translate_text(text: str) -> str:
        return {"Rubrik": "Heading", "Brödtext": "Body text"}[text]

    monkeypatch.setattr(translate_module, "translate_text", fake_translate_text)

    translated = asyncio.run(translate_module.translate_embed(embed, ("title", "description")))

    assert translated is not embed
    assert translated.title == "Heading"
    assert translated.description == "Body text"
    assert translated.url == embed.url
    assert translated.media == embed.media


def test_translate_embed_leaves_empty_fields_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that empty fields are not sent to the translator."""
    embed = Embed(
        title="",
        description="",
        url="https://twitter.com/example/status/1",
        media=(),
    )

    def fake_translate_text(text: str) -> str:
        msg = "translate_text must not be called for empty fields"
        raise AssertionError(msg)

    monkeypatch.setattr(translate_module, "translate_text", fake_translate_text)

    translated = asyncio.run(translate_module.translate_embed(embed, ("title", "description")))

    assert translated is embed
