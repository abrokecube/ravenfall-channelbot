# ruff: noqa: CPY001
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import cast

from bot.clients.ravenfall_multichat import RavenfallMultichatClient


def get_multichat_url() -> str:
    """Read the ravenfall-multichat base URL from config.toml."""
    with Path("config.toml").open("rb") as f:
        config = tomllib.load(f)
    return cast("str", config["services"]["ravenfall_multichat"]["url"])


def get_multichat_client() -> RavenfallMultichatClient:
    """Create a RavenfallMultichatClient configured from config.toml."""
    return RavenfallMultichatClient(get_multichat_url())


def get_ravenfall_credentials() -> tuple[str, str]:
    """Return the Ravenfall API username and password.

    Reads the ``BOT_INTEGRATIONS_RAVENFALL_USERNAME`` / ``_PASSWORD``
    environment variables first (matching the bot's config override order),
    falling back to ``[integrations.ravenfall]`` in config.toml.
    """
    with Path("config.toml").open("rb") as f:
        config = tomllib.load(f)
    table = config.get("integrations", {}).get("ravenfall", {})
    username = os.getenv("BOT_INTEGRATIONS_RAVENFALL_USERNAME") or table.get("username") or ""
    password = os.getenv("BOT_INTEGRATIONS_RAVENFALL_PASSWORD") or table.get("password") or ""
    return username, password

