"""Captured-request regression: relay API `data` must be a UTF-8 JSON string.

The relay marshals `data` through `json.RawMessage`, so the nested value
must be a string. Passing `msgspec` bytes to `aiohttp`'s `json=` body fails
before any network request with `TypeError: Object of type bytes is not
JSON serializable`.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from aiohttp import web

from bot.clients.ravenfall_middleman import (
    MiddlemanClient,
    RavenBotMessage,
    RavenfallMessage,
    Recipient,
    Sender,
)


def make_sender() -> Sender:
    """Minimal sender for captured-request probes."""
    return Sender(
        id="",
        character_id="",
        username="t",
        display_name="t",
        color=None,
        platform="twitch",
        platform_id="1",
        is_broadcaster=False,
        is_moderator=False,
        is_subscriber=False,
        is_vip=False,
        is_game_administrator=False,
        is_game_moderator=False,
        sub_tier=0,
        identifier="1",
    )


async def _run() -> dict:
    seen: dict = {}

    async def capture(request: web.Request) -> web.Response:
        body = await request.json()
        seen.update(body)
        return web.json_response({"success": True})

    app = web.Application()
    app.router.add_post("/api/send-to-client", capture)
    app.router.add_post("/api/send-to-server", capture)
    app.router.add_post("/api/send-to-server-and-wait", capture)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    client = MiddlemanClient(f"http://127.0.0.1:{port}")
    await client.send_to_ravenbot(
        "rf_test",
        RavenfallMessage(
            identifier="message",
            recipient=Recipient(
                user_id="",
                character_id="",
                platform="twitch",
                platform_id="1",
                platform_user_name="t",
            ),
            format="hi",
            args=[],
            tags=[],
            category="system",
            correlation_id="cap-1",
        ),
    )
    assert isinstance(seen.get("data"), str), (
        f"send-to-client data must be str, got {type(seen.get('data'))}"
    )
    json.loads(seen["data"])

    await client.send_to_ravenfall(
        "rf_test",
        RavenBotMessage(
            identifier="join",
            sender=make_sender(),
            content="{}",
            correlation_id="cap-2",
        ),
    )
    assert isinstance(seen.get("data"), str), (
        f"send-to-server data must be str, got {type(seen.get('data'))}"
    )
    json.loads(seen["data"])
    await runner.cleanup()
    return seen


def test_relay_request_data_is_utf8_json_string():
    """Relay API `data` is a UTF-8 JSON string in captured requests."""
    seen = asyncio.run(_run())
    assert isinstance(seen["data"], str)
    assert "cap-2" in seen["data"]
