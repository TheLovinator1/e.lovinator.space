from __future__ import annotations

import time
from datetime import datetime  # ruff: ignore[typing-only-standard-library-import]
from enum import StrEnum
from ipaddress import IPv4Network  # ruff: ignore[typing-only-standard-library-import]

import wreq
from loguru import logger
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from wreq import Client
from wreq import Emulation

discord_ips_cache: DiscordIPs | None = None
discord_ips_cache_time: float = 0.0


class Service(StrEnum):
    """What service the prefix is for."""

    API = "api"
    MEDIA = "media"


class Prefix(BaseModel):
    """IP and if the IP is tied to API or media."""

    model_config = ConfigDict(populate_by_name=True)

    ipv4_prefix: IPv4Network = Field(alias="ipv4Prefix")
    services: list[Service]


class DiscordIPs(BaseModel):
    """Discord IPs."""

    model_config = ConfigDict(populate_by_name=True)

    creation_time: datetime = Field(alias="creationTime")
    """\"creationTime\": \"2026-08-04T18:01:19Z\""""

    sync_token: str = Field(alias="syncToken")
    """\"syncToken\": \"4ce19406\""""

    notes: str
    """\"notes\": \"discord egress\""""

    prefixes: list[Prefix]
    """\"prefixes\": [
        {"ipv4Prefix": "104.196.222.45/32", "services": ["media"]},
        {"ipv4Prefix": "34.138.218.50/32", "services": ["api"]},
    ]"""


async def get_discord_ips() -> DiscordIPs:
    """Grab the IPs that Discord uses.

    Returns:
        DiscordIPs: The IPs that Discord uses.
    """
    global discord_ips_cache, discord_ips_cache_time  # ruff: ignore[global-statement]

    now: float = time.monotonic()
    if discord_ips_cache is not None and (now - discord_ips_cache_time) < 3600.0:  # ruff: ignore[magic-value-comparison]
        return discord_ips_cache

    url = "https://cdn.discordapp.com/ipranges/discord.json"

    client = Client(emulation=Emulation.Chrome149)
    try:
        resp: wreq.Response = await client.get(url)
        data: str = await resp.text()
        parsed_data: DiscordIPs = DiscordIPs.model_validate_json(data)
    except Exception:
        if discord_ips_cache is not None:
            return discord_ips_cache
        raise

    discord_ips_cache = parsed_data
    discord_ips_cache_time = now
    return parsed_data


if __name__ == "__main__":
    import asyncio

    # Log the IPs that Discord uses.
    ips: DiscordIPs = asyncio.run(get_discord_ips())
    for ip in ips.prefixes:
        for service in ip.services:
            logger.info(f"[{service}]\t{ip.ipv4_prefix}")
