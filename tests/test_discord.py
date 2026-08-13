import asyncio
import json
from ipaddress import IPv4Network
from typing import TYPE_CHECKING

import e.discord as discord_module
from e.discord import DiscordIPs
from e.discord import Service

if TYPE_CHECKING:
    import pytest
    import wreq

SAMPLE_PAYLOAD: dict[str, str | list[dict[str, str | list[str]]]] = {
    "creationTime": "2026-08-04T18:01:19Z",
    "syncToken": "4ce19406",
    "notes": "discord egress",
    "prefixes": [
        {"ipv4Prefix": "104.196.222.45/32", "services": ["media"]},
        {"ipv4Prefix": "34.138.218.50/32", "services": ["api"]},
    ],
}


def test_discord_ips_model_validate_json_parses_aliases() -> None:
    """Test that DiscordIPs.model_validate_json parses aliases."""
    parsed: DiscordIPs = DiscordIPs.model_validate_json(json.dumps(SAMPLE_PAYLOAD))

    assert parsed.creation_time.isoformat() == "2026-08-04T18:01:19+00:00"
    assert parsed.sync_token == "4ce19406"
    assert parsed.notes == "discord egress"
    assert parsed.prefixes[0].ipv4_prefix == IPv4Network("104.196.222.45/32")
    assert parsed.prefixes[0].services == [Service.MEDIA]
    assert parsed.prefixes[1].services == [Service.API]


def test_get_discord_ips_fetches_and_validates_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that get_discord_ips fetches the payload and validates it."""

    class FakeResponse:
        async def text(self) -> str:
            return json.dumps(SAMPLE_PAYLOAD)

    class FakeClient:
        def __init__(self, *, emulation: wreq.Emulation) -> None:
            assert emulation == discord_module.Emulation.Chrome149

        async def get(self, url: str) -> FakeResponse:
            assert url == "https://cdn.discordapp.com/ipranges/discord.json"
            return FakeResponse()

    monkeypatch.setattr(discord_module, "Client", FakeClient)

    parsed: DiscordIPs = asyncio.run(discord_module.get_discord_ips())

    assert isinstance(parsed, DiscordIPs)
    assert parsed.notes == "discord egress"
    assert [prefix.ipv4_prefix for prefix in parsed.prefixes] == [
        IPv4Network("104.196.222.45/32"),
        IPv4Network("34.138.218.50/32"),
    ]
